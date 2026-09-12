from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.advanced import (
    ReminderDraft,
    parse_budget_text,
    parse_installment_text,
    parse_recurring_text,
    parse_reminder_text,
)
from app.domain.transactions import TransactionDraft, parse_transaction_text
from app.infrastructure.repository import FinanceRepository
from app.integrations.hermes import HermesInterpretationError, HermesInterpreter

logger = logging.getLogger(__name__)
BRAZIL_TIMEZONE = ZoneInfo("America/Sao_Paulo")


@dataclass(frozen=True)
class FinanceResult:
    reply: str
    transaction: TransactionDraft | None
    duplicate: bool = False


_CATEGORY_LABELS = {
    "alimentacao": "Alimentação",
    "transporte": "Transporte",
    "moradia": "Moradia",
    "lazer": "Lazer",
    "saude": "Saúde",
    "salario": "Salário",
    "outros": "Outros",
}

_CATEGORY_ICONS = {
    "alimentacao": "🍽️",
    "transporte": "🚗",
    "moradia": "🏠",
    "lazer": "🎮",
    "saude": "🩺",
    "salario": "💼",
    "outros": "📌",
}


class FinanceService:
    def __init__(self, repository: FinanceRepository | None = None) -> None:
        self.repository = repository or FinanceRepository()
        self._initialized = False
        self._initialization_lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialization_lock:
            if not self._initialized:
                await self.repository.initialize()
                self._initialized = True

    async def close(self) -> None:
        await self.repository.close()

    @staticmethod
    def _task_title(message: str) -> str:
        title = re.sub(r"\s+", " ", (message or "").strip(" .,!?:;\t"))
        return title[:1].upper() + title[1:]

    async def _create_reminder_task(
        self,
        phone: str,
        reminder: ReminderDraft,
        source_message_id: str | None,
        recurring: bool = False,
    ) -> tuple[int, str]:
        title = self._task_title(reminder.message)
        if recurring:
            now = datetime.now(BRAZIL_TIMEZONE)
            task_id = await self.repository.create_task_and_reminder(
                phone,
                title,
                now + timedelta(hours=1),
                source_message_id=source_message_id,
                repeat_interval_minutes=60,
                repeat_until=now + timedelta(days=2),
            )
            return task_id, (
                f"✅ Tarefa adicionada: *{title}*.\n\n"
                "Vou lembrar você a cada 1 hora durante 2 dias.\n"
                "Para parar, responda *parar lembrete*. "
                "Para ver suas tarefas, peça *minha lista de tarefas*."
            )
        task_id = await self.repository.create_task_and_reminder(
            phone,
            title,
            reminder.due_at,
            source_message_id=source_message_id,
        )
        return task_id, f"⏰ Lembrete criado para {reminder.due_at.strftime('%d/%m às %H:%M')}: *{title}*."

    async def process_message(
        self,
        message: str,
        phone: str,
        today: date | None = None,
        message_id: str | None = None,
    ) -> FinanceResult:
        await self.initialize()
        first_message = await self.repository.ensure_user(phone)
        history = await self.repository.recent_conversation(phone)
        if not await self.repository.mark_message_once(message_id, phone):
            return FinanceResult("", None, duplicate=True)
        await self.repository.append_conversation_message(phone, "user", message, message_id)

        try:
            current_day = today or datetime.now(BRAZIL_TIMEZONE).date()
            normalized = self._normalize(message)
            await self.repository.mark_welcomed(phone)
            await self.repository.expire_pending_confirmations(datetime.now(UTC))

            await self.repository.expire_pending_reminders(datetime.now(UTC))
            if self._is_stop_reminder_request(normalized):
                cancelled = await self.repository.cancel_active_reminders(phone)
                pending_reminder = await self.repository.get_pending_reminder(phone)
                if pending_reminder:
                    await self.repository.complete_pending_reminder(pending_reminder["id"], "cancelled")
                if cancelled or pending_reminder:
                    return FinanceResult(
                        self._with_welcome(
                            "🛑 Parei os lembretes ativos. A tarefa continua disponível na sua lista; "
                            "quando concluir, peça *concluir tarefa* ou *já fiz*.",
                            first_message,
                        ),
                        None,
                    )
                return FinanceResult(
                    self._with_welcome("ℹ️ Não encontrei lembretes ativos para parar.", first_message),
                    None,
                )
            if self._is_task_list_query(normalized):
                tasks = await self.repository.list_tasks(phone)
                if not tasks:
                    reply = "📋 Você não tem tarefas pendentes."
                else:
                    lines = [f"{index}. {task['title']}" for index, task in enumerate(tasks, start=1)]
                    reply = (
                        "📋 *Sua lista de tarefas*\n\n"
                        + "\n".join(lines)
                        + "\n\nPara concluir: *concluir tarefa 1*.\n"
                        "Para parar lembretes: *parar lembrete*."
                    )
                return FinanceResult(self._with_welcome(reply, first_message), None)
            if self._is_complete_task_request(normalized):
                task_match = re.search(r"(?:tarefa\s*)?(\d+)\b", normalized)
                task_id = None
                if task_match:
                    task_index = int(task_match.group(1))
                    task_id = -1
                    tasks = await self.repository.list_tasks(phone)
                    if 1 <= task_index <= len(tasks):
                        task_id = tasks[task_index - 1]["id"]
                completed = await self.repository.complete_task(phone, task_id)
                if completed:
                    return FinanceResult(
                        self._with_welcome(f"✅ Marquei {completed} tarefa(s) como concluída(s) e parei seus lembretes.", first_message),
                        None,
                    )
                return FinanceResult(self._with_welcome("ℹ️ Não encontrei tarefa pendente para concluir.", first_message), None)

            pending_reminder = await self.repository.get_pending_reminder(phone)
            if pending_reminder:
                if normalized in {"cancelar", "cancela", "não", "nao"}:
                    await self.repository.complete_pending_reminder(pending_reminder["id"], "cancelled")
                    return FinanceResult("✅ Tudo bem. Não criei esse lembrete.", None)
                if self._has_explicit_reminder_schedule(message):
                    try:
                        reminder = parse_reminder_text(
                            f"me lembre de {pending_reminder['message']} {message}"
                        )
                    except ValueError:
                        reminder = None
                    if reminder:
                        await self._create_reminder_task(
                            phone,
                            reminder,
                            pending_reminder["source_message_id"],
                        )
                        await self.repository.complete_pending_reminder(pending_reminder["id"])
                        return FinanceResult(
                            self._with_welcome(
                                f"⏰ Lembrete criado para {reminder.due_at.strftime('%d/%m às %H:%M')}: *{self._task_title(reminder.message)}*.",
                                first_message,
                            ),
                            None,
                        )
                if self._is_reminder_day_follow_up(normalized):
                    return FinanceResult(
                        self._with_welcome(
                            "⏰ Perfeito. Qual horário devo usar? Exemplo: *18:30*.",
                            first_message,
                        ),
                        None,
                    )

            pending = await self.repository.get_pending_confirmation(phone)
            correction_text = self._correction_text(normalized)
            if pending and correction_text is not None:
                values = self._parse_correction_values(correction_text)
                if not values:
                    return FinanceResult("Informe a correção, por exemplo: *corrigir categoria alimentacao* ou *corrigir valor 50*.", None)
                await self.repository.update_confirmation(pending["id"], values)
                return FinanceResult("✅ Corrigi a pendência. Responda *confirmar* para registrar ou *cancelar* para descartar.", None)
            if pending and normalized in {"confirmar", "confirma", "sim", "cancelar", "cancela", "não", "nao"}:
                if normalized in {"cancelar", "cancela", "não", "nao"}:
                    await self.repository.update_confirmation_status(pending["id"], "cancelled")
                    return FinanceResult("✅ Tudo bem. Não registrei esse lançamento.", None)
                confirmed = await self.repository.confirm_pending_confirmation(pending["id"], phone, current_day)
                if confirmed is None:
                    return FinanceResult("ℹ️ Essa confirmação já foi processada ou expirou.", None)
                transaction = TransactionDraft(confirmed["intent"].replace("create_", ""), confirmed["amount_cents"] / 100, confirmed["description"], confirmed["category"], confirmed["payment_method"])
                return FinanceResult(self._registration_reply(transaction, current_day), transaction)
            if correction_text is not None:
                values = self._parse_correction_values(correction_text)
                latest_transaction = await self.repository.get_latest_transaction(phone)
                if values and latest_transaction:
                    await self.repository.update_transaction(phone, latest_transaction["id"], values)
                    if "category" in values:
                        label = _CATEGORY_LABELS.get(values["category"], "Outros")
                        return FinanceResult(f"✅ Atualizei o último lançamento para a categoria *{label}*.", None)
                    return FinanceResult("✅ Atualizei o último lançamento. Consulte ou corrija novamente se precisar.", None)

            if correction_text is not None or normalized in {"confirmar", "confirma", "sim", "cancelar", "cancela", "não", "nao"}:
                latest = await self.repository.get_latest_confirmation(phone)
                if latest and latest["status"] == "expired":
                    return FinanceResult("⌛ A confirmação expirou após 24 horas. Envie o lançamento novamente para criar uma nova confirmação.", None)
                return FinanceResult("ℹ️ Não há lançamento pendente para confirmar ou alterar.", None)

            if self._is_greeting(normalized):
                salary = await self.repository.get_salary(phone)
                prompt = "\n\nPara começar seu planejamento, qual é o seu salário mensal? Exemplo: *Meu salário é R$ 3.000*." if salary is None else ""
                return FinanceResult(self._welcome_message() + prompt, None)

            if self._is_salary_message(normalized):
                salary_transaction = parse_transaction_text(message)
                await self.repository.set_salary(phone, round(salary_transaction.amount * 100))
                reply = f"✅ Salário mensal salvo: *{self._money(salary_transaction.amount)}*. Agora vou acompanhar seus gastos e avisar a cada 10% do salário utilizado."
                return FinanceResult(self._with_welcome(reply, first_message), None)

            if self._is_reminder_request(normalized):
                has_schedule = self._has_explicit_reminder_schedule(message)
                reminder_from_hermes = False
                try:
                    reminder = parse_reminder_text(message)
                except ValueError:
                    try:
                        interpreted = await HermesInterpreter().interpret(phone, message, history=history)
                    except HermesInterpretationError:
                        return FinanceResult(
                            self._with_welcome(
                                "⏰ Claro! Diga o que devo lembrar e para quando. "
                                "Exemplo: *me lembre de pagar a conta hoje às 18:30*.",
                                first_message,
                            ),
                            None,
                        )
                    reminder = self._reminder_from_hermes(interpreted)
                    reminder_from_hermes = reminder is not None
                    if reminder is None:
                        reminder_text = str(interpreted.get("reminder_text") or "").strip()
                        if reminder_text and not self._is_date_only_reminder(reminder_text):
                            if self._has_explicit_reminder_date(message) and not has_schedule:
                                await self.repository.save_pending_reminder(phone, reminder_text, message_id)
                                return FinanceResult(
                                    self._with_welcome(
                                        "⏰ Entendi a data. Qual horário devo usar? Exemplo: *10:00*.",
                                        first_message,
                                    ),
                                    None,
                                )
                            reminder = ReminderDraft(reminder_text, datetime.now(BRAZIL_TIMEZONE))
                            _, reply = await self._create_reminder_task(
                                phone,
                                reminder,
                                message_id,
                                recurring=not has_schedule,
                            )
                            return FinanceResult(self._with_welcome(reply, first_message), None)
                        return FinanceResult(
                            self._with_welcome(
                                str(interpreted.get("reply") or "⏰ Diga o que devo lembrar e para quando."),
                                first_message,
                            ),
                            None,
                        )
                if not has_schedule and not reminder_from_hermes:
                    _, reply = await self._create_reminder_task(
                        phone,
                        reminder,
                        message_id,
                        recurring=True,
                    )
                    return FinanceResult(self._with_welcome(reply, first_message), None)
                _, reply = await self._create_reminder_task(
                    phone,
                    reminder,
                    message_id,
                )
                return FinanceResult(self._with_welcome(reply, first_message), None)

            if self._is_recurring_query(normalized):
                return FinanceResult(await self._recurring_summary(phone), None)

            if "todo dia" in normalized or "cada dia" in normalized:
                recurring = parse_recurring_text(message)
                await self.repository.add_recurring(phone, recurring)
                return FinanceResult(self._with_welcome(f"🔁 Despesa recorrente criada: *{recurring.description}*, {self._money(recurring.amount)} todo dia {recurring.day_of_month}.", first_message), None)

            if " vezes" in normalized and " em " in normalized:
                installment = parse_installment_text(message)
                await self.repository.add_installment(phone, installment, current_day)
                return FinanceResult(self._with_welcome(f"🧾 Compra parcelada registrada: *{installment.description}*, {installment.installment_count} parcelas de aproximadamente *{self._money(installment.total_amount / installment.installment_count)}*.", first_message), None)

            if normalized.startswith(("meu limite", "minha meta")):
                budget = parse_budget_text(message)
                await self.repository.set_budget(phone, budget, current_day.strftime("%Y-%m"))
                return FinanceResult(self._with_welcome(f"🎯 Limite salvo para *{budget.category}*: {self._money(budget.limit_amount)}.", first_message), None)

            if self._is_count_query(normalized):
                reply = await self._count_expenses(phone)
                return FinanceResult(self._with_welcome(reply, first_message), None)

            if self._is_period_query(normalized):
                reply = await self._period_summary(phone, current_day, normalized)
                return FinanceResult(self._with_welcome(reply, first_message), None)

            if self._is_category_query(normalized):
                reply = await self._category_summary(phone, current_day, normalized)
                return FinanceResult(self._with_welcome(reply, first_message), None)

            if self._is_spending_query(normalized):
                reply = await self._spending_summary(phone)
                return FinanceResult(self._with_welcome(reply, first_message), None)

            try:
                transaction = parse_transaction_text(message)
                if not self._is_clear_transaction_message(normalized):
                    raise ValueError("não identifiquei um registro financeiro claro")
                if self._is_ambiguous_message(normalized):
                    try:
                        interpreted = await HermesInterpreter().interpret(phone, message, history=history)
                    except HermesInterpretationError:
                        logger.exception("failed to interpret ambiguous finance message")
                        return FinanceResult("🤔 Essa mensagem parece ambígua. Confirme o valor, a descrição e o pagamento antes de eu registrar.", None)
                    if interpreted.get("amount") is None:
                        return FinanceResult("🤔 Não consegui confirmar o valor dessa mensagem. Pode informar o valor exato?", None)
                    if interpreted["requires_confirmation"] or float(interpreted["confidence"]) < 0.85:
                        confirmation_id = message_id or f"local-{uuid.uuid4()}"
                        await self.repository.save_confirmation(phone, confirmation_id, interpreted)
                        return FinanceResult(f"🤔 Posso registrar *{interpreted.get('description') or 'este lançamento'}* de *{self._money(float(interpreted['amount']))}*. Responda *confirmar* ou *cancelar*.", None)
                    if interpreted["intent"] not in {"create_expense", "create_income"}:
                        return FinanceResult("🤔 Não consegui classificar esse lançamento com segurança. Pode confirmar os dados?", None)
                    transaction = TransactionDraft(
                        "income" if interpreted["intent"] == "create_income" else "expense",
                        float(interpreted["amount"]),
                        interpreted.get("description") or "não informado",
                        interpreted.get("category") or "outros",
                        interpreted.get("payment_method") or "não informado",
                    )
            except ValueError as local_error:
                try:
                    interpreted = await HermesInterpreter().interpret(phone, message, history=history)
                except HermesInterpretationError:
                    raise local_error
                if interpreted.get("amount") is None:
                    intent = interpreted.get("intent")
                    if intent == "query_category" and interpreted.get("category"):
                        reply = await self._category_summary(phone, current_day, interpreted["category"])
                        return FinanceResult(reply, None)
                    if intent in {"query_summary", "query"}:
                        reply = await self._spending_summary(phone)
                        return FinanceResult(reply, None)
                    if intent in {"list_recurring", "query_recurring"}:
                        reply = await self._recurring_summary(phone)
                        return FinanceResult(reply, None)
                    if interpreted.get("reply"):
                        return FinanceResult(str(interpreted["reply"]), None)
                    raise
                if interpreted["requires_confirmation"] or float(interpreted["confidence"]) < 0.85:
                    confirmation_id = message_id or f"local-{uuid.uuid4()}"
                    if interpreted.get("amount") is not None:
                        await self.repository.save_confirmation(phone, confirmation_id, interpreted)
                    return FinanceResult(f"🤔 Posso registrar *{interpreted.get('description') or 'este lançamento'}* de *{self._money(float(interpreted.get('amount') or 0))}*. Responda *confirmar* ou *cancelar*.", None)
                if (
                    interpreted["intent"] not in {"create_expense", "create_income"}
                    or interpreted.get("amount") is None
                    or not self._is_clear_transaction_message(normalized)
                ):
                    if interpreted.get("reply"):
                        return FinanceResult(str(interpreted["reply"]), None)
                    return FinanceResult(
                        "🤔 Posso registrar gastos quando você me disser claramente o que foi pago ou comprado. "
                        "Não registrei nada nesta mensagem.",
                        None,
                    )
                transaction = TransactionDraft(
                    "income" if interpreted["intent"] == "create_income" else "expense",
                    float(interpreted["amount"]),
                    interpreted.get("description") or "não informado",
                    interpreted.get("category") or "outros",
                    interpreted.get("payment_method") or "não informado",
                )
            await self.repository.add_transaction(phone, transaction, current_day)
            reply = self._registration_reply(transaction, current_day)
            if transaction.type == "expense":
                reply += await self._salary_alert(phone, current_day)
                reply += await self._budget_alert(phone, transaction.category, current_day)
            if await self.repository.get_salary(phone) is None:
                reply += "\n\n💡 Se quiser acompanhar seu orçamento, informe seu salário mensal."
            return FinanceResult(self._with_welcome(reply, first_message), transaction)
        except ValueError as exc:
            reply = (
                "🤔 Posso registrar sua movimentação, mas preciso do valor.\n\n"
                f"{exc}.\n\n"
                "Exemplo: *Gastei R$ 42 no almoço*."
            )
            return FinanceResult(self._with_welcome(reply, first_message), None)
        except Exception:
            logger.exception("unexpected finance message processing failure")
            reply = (
                "⚠️ Tive uma pequena dificuldade para concluir isso agora.\n\n"
                "Seus dados não foram alterados. Tente novamente em alguns instantes."
            )
            return FinanceResult(self._with_welcome(reply, first_message), None)

    @staticmethod
    def _normalize(message: str) -> str:
        return re.sub(r"\s+", " ", (message or "").strip().casefold())

    @staticmethod
    def _is_greeting(message: str) -> bool:
        return message in {"oi", "olá", "ola", "olá!", "ola!", "bom dia", "boa tarde", "boa noite", "menu", "ajuda"}

    @staticmethod
    def _is_ambiguous_message(message: str) -> bool:
        markers = ("por volta", "aproximadamente", "acho que", "talvez", "não lembro", "nao lembro", "uma coisa", "alguma coisa")
        return any(marker in message for marker in markers)

    @classmethod
    def _correction_text(cls, message: str) -> str | None:
        match = re.match(r"^(?:corrigir|corrija|ajustar|ajuste|alterar|altere)\s+(.+)$", message)
        return match.group(1).strip() if match else None

    @staticmethod
    def _parse_correction_values(correction: str) -> dict[str, object]:
        correction_key = unicodedata.normalize("NFKD", correction).encode("ascii", "ignore").decode("ascii")
        values: dict[str, object] = {}
        category_pattern = "|".join(_CATEGORY_LABELS)
        category_match = re.search(
            rf"(?:^|\s)(?:a\s+)?categoria\s+(?:(?:para|de|como)\s+)?(?P<category>{category_pattern})(?:$|\s)",
            correction_key,
        )
        if category_match:
            values["category"] = category_match.group("category")
        elif correction_key in _CATEGORY_LABELS:
            values["category"] = correction_key
        elif correction_key.startswith(("para ", "de ", "como ")):
            category = correction_key.split(" ", 1)[1]
            if category in _CATEGORY_LABELS:
                values["category"] = category
        if "pix" in correction_key:
            values["payment_method"] = "Pix"
        amount_match = re.search(r"(?:r\$\s*)?([0-9]+(?:[.,][0-9]{1,2})?)", correction)
        if amount_match:
            values["amount_cents"] = round(float(amount_match.group(1).replace(",", ".")) * 100)
        return values

    @staticmethod
    def _is_stop_reminder_request(message: str) -> bool:
        return bool(
            re.search(
                r"\b(?:parar|pare|cancelar|cancele|desativar|desative)\b.*\b(?:lembrete|lembre|lembrar|avisos?)\b|"
                r"\b(?:não|nao)\s+me\s+(?:lembre|avise)\b",
                message,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _is_task_list_query(message: str) -> bool:
        return any(
            phrase in message
            for phrase in (
                "lista de tarefas",
                "minha lista de tarefas",
                "minhas tarefas",
                "tarefas pendentes",
                "o que tenho para fazer",
                "o que eu tenho para fazer",
                "meus lembretes",
            )
        )

    @staticmethod
    def _is_complete_task_request(message: str) -> bool:
        return bool(
            re.search(
                r"\b(?:concluir|concluí|conclui|finalizar|finalizei|feito|já fiz|ja fiz|terminei)\b",
                message,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_explicit_reminder_date(message: str) -> bool:
        return bool(
            re.search(
                r"\b(?:dia\s+\d{1,2}\s+de\s+|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|(?:segunda|terça|terca|quarta|quinta|sexta|sábado|sabado|domingo)(?:-feira|\s+feira)?)",
                message,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_explicit_reminder_schedule(message: str) -> bool:
        return bool(
            re.search(
                r"(?:\b(?:em|daqui\s+a)\s*\d+\s*(?:minutos?|mins?|m|horas?|h|dias?|d)\b|"
                r"\b(?:(?:hoje|amanhã|amanha)\s*[,;]?\s*)?(?:às|as|para|pra)\s+\d{1,2}(?:(?::|h)\d{2})?\s*(?:horas?|h)?(?:\s+da\s+(?:manhã|manha|tarde|noite))?\b|"
                r"\b\d{1,2}(?:(?::|h)\d{2})\b)",
                message,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _is_reminder_day_follow_up(message: str) -> bool:
        return bool(
            re.fullmatch(
                r"(?:hoje|amanhã|amanha)(?:\s+(?:de|pela)\s+(?:manhã|manha|tarde|noite))?",
                message,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _is_date_only_reminder(message: str) -> bool:
        return bool(re.fullmatch(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?", message.strip()))

    @staticmethod
    def _reminder_from_hermes(data: dict[str, object]) -> ReminderDraft | None:
        if data.get("intent") != "create_reminder":
            return None
        reminder_text = str(data.get("reminder_text") or "").strip()
        reminder_schedule = str(data.get("reminder_schedule") or "").strip()
        if not reminder_text or not reminder_schedule:
            return None
        try:
            return parse_reminder_text(f"me lembre de {reminder_text} {reminder_schedule}")
        except ValueError:
            return None

    @staticmethod
    def _is_clear_transaction_message(message: str) -> bool:
        if any(
            verb in message
            for verb in ("gastei", "paguei", "comprei", "recebi", "ganhei", "transferi")
        ):
            return True
        return bool(
            re.fullmatch(
                r"[\wÀ-ÿ$\s-]{2,60}\s+(?:r\$\s*)?\d+(?:[.,]\d{1,2})?",
                message,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _is_reminder_request(message: str) -> bool:
        return bool(
            re.search(
                r"\b(?:lembrete|lembrar|lembre|lembra|avise|avisa)\b",
                message,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _is_recurring_query(message: str) -> bool:
        return any(term in message for term in ("pagamento recorrente", "pagamentos recorrentes", "despesa recorrente", "despesas recorrentes", "recorrencia", "recorrência"))

    @staticmethod
    def _is_count_query(message: str) -> bool:
        return (
            "quantas despesas" in message
            or "quantos gastos" in message
            or "número de despesas" in message
            or "numero de despesas" in message
        )

    @staticmethod
    def _is_spending_query(message: str) -> bool:
        query_terms = ("quanto gastei", "quanto eu gastei", "resumo", "relatório", "relatorio", "meus gastos")
        return any(term in message for term in query_terms)

    async def _period_summary(self, phone: str, current_day: date, message: str) -> str:
        if "semana" in message:
            start = current_day - timedelta(days=current_day.weekday())
            label = "esta semana"
        elif "hoje" in message:
            start = current_day
            label = "hoje"
        else:
            start = current_day.replace(day=1)
            label = "este mês"
        category = self._category_from_message(message)
        count, cents = await self.repository.expense_summary(phone, start, current_day + timedelta(days=1), category=category)
        if not count:
            return f"📭 Ainda não encontrei despesas registradas para {label}."
        suffix = f" em {_CATEGORY_LABELS[category]}" if category else ""
        return f"📊 Você gastou *{self._money(cents / 100)}*{suffix} {label}, em *{count} lançamento(s)*."

    @staticmethod
    def _category_from_message(message: str) -> str | None:
        key = unicodedata.normalize("NFKD", message).encode("ascii", "ignore").decode("ascii")
        aliases = {
            "alimentacao": ("alimentacao", "comida", "mercado", "restaurante"),
            "transporte": ("transporte", "gasolina", "combustivel"),
            "moradia": ("moradia", "casa", "aluguel"),
            "lazer": ("lazer",),
            "saude": ("saude", "farmacia", "medico"),
            "salario": ("salario", "ordenado"),
            "outros": ("outros",),
        }
        return next((category for category, terms in aliases.items() if any(term in key for term in terms)), None)

    @classmethod
    def _is_category_query(cls, message: str) -> bool:
        key = unicodedata.normalize("NFKD", message).encode("ascii", "ignore").decode("ascii")
        return cls._category_from_message(message) is not None and any(
            term in key for term in ("categoria", "gastos de", "gastos com", "gastei com", "relatorio de", "resumo de")
        )

    async def _recurring_summary(self, phone: str) -> str:
        recurring = await self.repository.list_recurring(phone)
        active = [item for item in recurring if item["active"]]
        if not active:
            return "📭 Você ainda não tem pagamentos recorrentes cadastrados."
        lines = [
            f"• {item['description']}: {self._money(item['amount_cents'] / 100)} todo dia {item['day_of_month']}"
            for item in active
        ]
        return "🔁 *Seus pagamentos recorrentes*\n\n" + "\n".join(lines)

    async def _category_summary(self, phone: str, current_day: date, message: str) -> str:
        category = self._category_from_message(message)
        if not category:
            return "📊 Informe a categoria que deseja consultar."
        if "semana" in message:
            start = current_day - timedelta(days=current_day.weekday())
            label = "esta semana"
        elif "hoje" in message:
            start = current_day
            label = "hoje"
        else:
            start = current_day.replace(day=1)
            label = "este mês"
        count, cents = await self.repository.expense_summary(phone, start, current_day + timedelta(days=1), category=category)
        if not count:
            return f"📭 Ainda não encontrei despesas de {_CATEGORY_LABELS[category]} para {label}."
        return f"📊 Gastos em *{_CATEGORY_LABELS[category]}* {label}: *{self._money(cents / 100)}* em *{count} lançamento(s)*."

    @staticmethod
    def _is_period_query(message: str) -> bool:
        return "quanto gastei" in message and any(term in message for term in ("semana", "mês", "mes", "hoje"))

    @staticmethod
    def _is_salary_message(message: str) -> bool:
        return "salário" in message or "salario" in message or "ordenado" in message

    async def _budget_alert(self, phone: str, category: str, current_day: date) -> str:
        month_start = current_day.replace(day=1)
        next_month = date(current_day.year + (current_day.month == 12), 1 if current_day.month == 12 else current_day.month + 1, 1)
        status = await self.repository.budget_status(phone, category, month_start, next_month, current_day.strftime("%Y-%m"))
        if not status:
            return ""
        limit, spent = status
        percent = spent * 100 / limit
        if percent < 80:
            return ""
        if percent >= 100:
            message = "ultrapassou"
        elif percent >= 90:
            message = "chegou a 90%"
        else:
            message = "chegou a 80%"
        return f"\n\n⚠️ Seu limite de {_CATEGORY_LABELS.get(category, category)} {message}: *{self._money(spent / 100)}* de *{self._money(limit / 100)}*."

    async def _salary_alert(self, phone: str, current_day: date) -> str:
        salary = await self.repository.get_salary(phone)
        if not salary or salary <= 0:
            return ""
        month = current_day.strftime("%Y-%m")
        month_start = current_day.replace(day=1)
        next_month = date(current_day.year + (current_day.month == 12), 1 if current_day.month == 12 else current_day.month + 1, 1)
        spent = await self.repository.monthly_expense_total(phone, month_start, next_month)
        level = (spent * 10) // salary
        previous = await self.repository.get_salary_alert(phone, month)
        if level <= previous:
            return ""
        await self.repository.set_salary_alert(phone, month, level)
        percent = f"{spent * 100 / salary:.1f}".replace(".", ",")
        return f"\n\n📊 Você já utilizou *{percent}%* do seu salário neste mês ({self._money(spent / 100)} de {self._money(salary / 100)})."

    async def _count_expenses(self, phone: str) -> str:
        count, cents = await self.repository.count_expenses(phone)
        if not count:
            return (
                "📭 Ainda não encontrei despesas registradas para você.\n\n"
                "Quando quiser, envie algo como: *Gastei R$ 40 no almoço*."
            )
        noun = "despesa" if count == 1 else "despesas"
        verb = "registrada" if count == 1 else "registradas"
        entries = await self.repository.list_expenses(phone)
        lines = [
            f"{index}. {self._category_icon(entry['category'])} {entry['description']} — *{self._money(entry['amount_cents'] / 100)}*"
            for index, entry in enumerate(entries, start=1)
        ]
        listing = "\n".join(lines)
        suffix = "" if len(entries) == count else f"\n\nMostrando os {len(entries)} lançamentos mais recentes."
        return (
            f"📊 Você tem *{count} {noun} {verb}*, somando *{self._money(cents / 100)}*.\n\n"
            f"🧾 *Seus lançamentos:*\n{listing}{suffix}"
        )

    async def _spending_summary(self, phone: str) -> str:
        count, cents = await self.repository.count_expenses(phone)
        if not count:
            return (
                "📭 Ainda não encontrei despesas registradas para este período.\n\n"
                "Você pode começar enviando: *Gastei R$ 40 no almoço*."
            )
        return (
            "📊 *Resumo dos seus gastos*\n\n"
            f"💸 Total de despesas: *{self._money(cents / 100)}*\n"
            f"🧾 Lançamentos: *{count}*\n\n"
            "Se quiser, posso mostrar suas despesas recentes ou separar por categoria."
        )

    def _registration_reply(self, transaction: TransactionDraft, occurred_on: date) -> str:
        label = "Receita" if transaction.type == "income" else "Despesa"
        category = _CATEGORY_LABELS.get(transaction.category, "Outros")
        icon = _CATEGORY_ICONS.get(transaction.category, "📌")
        description = transaction.description if transaction.description != "não informado" else "não informada"
        return (
            f"✅ *{label} registrada com sucesso!*\n\n"
            f"💰 Valor: *{self._money(transaction.amount)}*\n"
            f"{icon} Categoria: *{category}*\n"
            f"📝 Descrição: {description}\n"
            f"💳 Pagamento: *{transaction.payment_method}*\n"
            f"📅 Data: {occurred_on.strftime('%d/%m/%Y')}\n\n"
            "Tudo certo por aqui 😊"
        )

    @staticmethod
    def _category_icon(category: str) -> str:
        return _CATEGORY_ICONS.get(category, "📌")

    @staticmethod
    def _money(value: float) -> str:
        return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    @staticmethod
    def _welcome_message() -> str:
        return (
            "👋 Oi! Eu sou o *Cofrin*, seu assistente financeiro 💰\n\n"
            "Pode falar comigo de forma natural — não precisa decorar comandos.\n\n"
            "*Como eu funciono:*\n"
            "• Texto: registro gastos e receitas.\n"
            "• Áudio: transcrevo sua mensagem e interpreto o pedido.\n"
            "• Imagem: leio comprovantes e tento identificar valor, categoria e pagamento.\n"
            "• Perguntas: consulto totais, períodos, categorias e recorrências.\n"
            "• Lembretes: aceito minutos, horas, dias e horários, como *hoje às 18:30*.\n"
            "• Dúvidas: quando faltar informação, pergunto antes de gravar.\n\n"
            "*Exemplos:*\n"
            "• *Gastei R$ 32 no almoço via Pix*\n"
            "• *Quanto gastei com alimentação este mês?*\n"
            "• *Me lembre de pagar a conta em 10 minutos*\n"
            "• *Criar lembrete de revisar o orçamento às 18:30*\n"
            "• *Consultar pagamentos recorrentes*\n\n"
            "Se eu interpretar um lançamento com dúvida, vou pedir sua confirmação. "
            "Você também pode corrigir a categoria depois. 😊"
        )

    @staticmethod
    def _with_welcome(reply: str, first_message: bool) -> str:
        if not first_message:
            return reply
        return f"{FinanceService._welcome_message()}\n\n━━━━━━━━━━━━\n\n{reply}"
