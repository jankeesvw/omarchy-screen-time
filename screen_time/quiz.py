"""Questions that buy minutes.

Two rules shape this file. The answer is never sent to the client, because the
client runs on the child's own machine and a text editor is not a challenge.
And the questions are weighted by what went wrong before, so the table that is
actually missing comes back around instead of the one they already know.
"""

import random
import secrets
import time

SIGNS = {"add": "+", "sub": "-", "mul": "×", "div": "÷"}


def _key(op, left, right):
    return f"{op}:{left}:{right}"


class Question:
    __slots__ = ("id", "op", "left", "right", "answer", "key", "text", "issued_at")

    def __init__(self, op, left, right, answer, issued_at):
        self.id = secrets.token_hex(8)
        self.op = op
        self.left = left
        self.right = right
        self.answer = answer
        self.key = _key(op, left, right)
        self.text = f"{left} {SIGNS[op]} {right}"
        self.issued_at = issued_at

    def public(self, reward_seconds, timeout_seconds):
        return {
            "id": self.id,
            "text": self.text,
            "op": self.op,
            "reward_seconds": reward_seconds,
            "timeout_seconds": timeout_seconds,
        }


class Quiz:
    """Generates questions for one account and remembers how it went."""

    def __init__(self, earn_config, stats=None, rng=None):
        self.config = earn_config
        self.stats = stats if isinstance(stats, dict) else {}
        self.rng = rng or random.Random()
        self.pending = None
        self.last_key = None

    # candidates ---------------------------------------------------------

    def _candidates(self):
        ops = self.config["ops"]
        tables = self.config["tables"]
        top = self.config["max_term"]
        out = []
        for op in ops:
            if op == "mul":
                out += [(op, a, b) for a in tables for b in range(1, 11)]
            elif op == "div":
                out += [(op, a * b, b) for a in tables for b in range(1, 11) if b]
            elif op == "add":
                out += [(op, a, b) for a in range(2, min(top, 100) + 1, 1)
                        for b in (self.rng.randint(2, min(top, 100)),)]
            elif op == "sub":
                out += [(op, a, b) for a in range(2, min(top, 100) + 1, 1)
                        for b in (self.rng.randint(1, a),)]
        return out

    def _weight(self, op, left, right):
        record = self.stats.get(_key(op, left, right)) or {}
        seen = max(0, int(record.get("seen", 0)))
        wrong = max(0, int(record.get("wrong", 0)))
        weight = 1.0
        if self.config.get("drill_weak") and seen:
            weight += 4.0 * (wrong / seen)
            if record.get("last_wrong") and time.time() - record["last_wrong"] < 86400:
                weight += 2.0
        return weight

    @staticmethod
    def _solve(op, left, right):
        if op == "add":
            return left + right
        if op == "sub":
            return left - right
        if op == "mul":
            return left * right
        return left // right

    def next_question(self, now=None):
        now = now or time.time()
        candidates = self._candidates()
        if not candidates:
            return None
        pool = [c for c in candidates if _key(*c) != self.last_key] or candidates
        weights = [self._weight(*c) for c in pool]
        op, left, right = self.rng.choices(pool, weights=weights, k=1)[0]
        question = Question(op, left, right, self._solve(op, left, right), now)
        self.pending = question
        self.last_key = question.key
        return question

    # answering ----------------------------------------------------------

    def answer(self, question_id, given, now=None):
        """Judge an answer. Returns a verdict dict; never leaks the answer of a
        question that is still open."""
        now = now or time.time()
        question = self.pending
        if not question or question.id != question_id:
            return {"ok": False, "error": "no_such_question"}

        elapsed = now - question.issued_at
        if elapsed > self.config["question_timeout_seconds"]:
            self.pending = None
            return {"ok": False, "error": "expired", "text": question.text}

        try:
            value = int(str(given).strip())
        except (TypeError, ValueError):
            return {"ok": False, "error": "not_a_number"}

        if elapsed < self.config["min_answer_seconds"]:
            return {"ok": False, "error": "too_fast",
                    "wait_seconds": round(self.config["min_answer_seconds"] - elapsed, 1)}

        correct = value == question.answer
        self._record(question, correct, now)
        self.pending = None
        return {
            "ok": True,
            "correct": correct,
            "text": question.text,
            "answer": question.answer,
            "given": value,
            "seconds_taken": round(elapsed, 1),
        }

    def _record(self, question, correct, now):
        record = self.stats.setdefault(question.key, {"seen": 0, "wrong": 0})
        record["seen"] = int(record.get("seen", 0)) + 1
        if not correct:
            record["wrong"] = int(record.get("wrong", 0)) + 1
            record["last_wrong"] = round(now, 1)

    def weakest(self, limit=5):
        """The keys worth practising, for the parent panel."""
        rows = []
        for key, record in self.stats.items():
            seen = int(record.get("seen", 0))
            wrong = int(record.get("wrong", 0))
            if seen >= 2 and wrong:
                op, left, right = key.split(":")
                rows.append({
                    "text": f"{left} {SIGNS.get(op, '?')} {right}",
                    "seen": seen,
                    "wrong": wrong,
                    "rate": round(wrong / seen, 2),
                })
        rows.sort(key=lambda row: (row["rate"], row["wrong"]), reverse=True)
        return rows[:limit]
