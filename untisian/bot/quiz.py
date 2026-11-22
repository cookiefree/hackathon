"""Vocabulary tests: /quiz, /words, and answering in the chat."""
from untisian.bot.common import Reply
from untisian.core import accounts, vocab
from untisian.core.materials import material_text



class QuizCommands:
    # --- vocabulary tests ---------------------------------------------------------------------

    @staticmethod
    def _quiz_args(args: tuple[str, ...]) -> tuple[int | None, bool]:
        mid, reverse = None, False
        for a in args:
            if a.lower() in ("reverse", "back", "r"):
                reverse = True
            elif a.lstrip("#").isdecimal():
                mid = int(a.lstrip("#"))
        return mid, reverse

    def needs_build(self, uid: int, *args: str) -> bool:
        """True when /quiz or /words will have to read the page first, which takes a few seconds."""
        owner = self.resolve(uid)
        mid, _ = self._quiz_args(args)
        return bool(owner and mid is not None and vocab.set_for_material(mid, owner) is None
                    and material_text(mid, owner))

    def _vocab_set(self, uid: int, owner: str, mid: int | None):
        """The word set for an upload (read from the photo on first use), or a Reply explaining the problem."""
        if mid is None:
            latest = vocab.latest_set(owner)
            if latest is None:
                return Reply("Send me a vocabulary page first (pick the kind 'vocab'), then /quiz NUMBER.")
            return latest
        got = material_text(mid, owner)
        if got is None:
            return Reply("No upload with that number.")
        if vocab.set_for_material(mid, owner) is None and not accounts.action_allowed("vocab", uid):
            return Reply("That's a lot of pages in a short time. Wait a few minutes and try again.")
        try:
            return vocab.build_set(mid, owner)
        except vocab.VocabError as e:
            return Reply(str(e))

    def _title_of(self, vset, owner: str) -> str:
        got = material_text(vset["material_id"], owner)
        return (got[0].title or f"#{got[0].id} {got[0].kurs}") if got else f"#{vset['material_id']}"

    def words(self, uid: int, *args: str) -> Reply:
        owner = self.resolve(uid)
        if owner is None:
            return self.refuse(uid)
        mid, _ = self._quiz_args(args)
        vset = self._vocab_set(uid, owner, mid)
        if isinstance(vset, Reply):
            return vset
        rows = vocab.items_of(vset["id"])
        head = f"{self._title_of(vset, owner)}: {len(rows)} words ({vset['lang_front'] or '?'} -> {vset['lang_back'] or '?'})"
        return Reply(head + "\n\n" + "\n".join(f"{r['front']} = {r['back']}" for r in rows))

    def quiz(self, uid: int, *args: str) -> Reply:
        owner = self.resolve(uid)
        if owner is None:
            return self.refuse(uid)
        mid, reverse = self._quiz_args(args)
        with self._lock:
            s = self._s(uid)
            if s.pending:
                return Reply("You have photos waiting. Finish them (or send /cancel) first.")
            if s.state:
                return Reply("Finish what you're doing first, or send /cancel.")
        vset = self._vocab_set(uid, owner, mid)
        if isinstance(vset, Reply):
            return vset
        return self._quiz_begin(uid, vocab.make_quiz(vset, self._title_of(vset, owner), reverse), reverse)

    def _quiz_begin(self, uid: int, quiz, reverse: bool) -> Reply:
        quiz.reverse = reverse
        with self._lock:
            s = self._s(uid)
            s.quiz, s.last_quiz, s.state = quiz, None, "quiz"
        vocab.save_quiz(uid, quiz)
        n = len(quiz.cards)
        return Reply(f"Vocabulary test: {quiz.title}\n{n} word{'s' if n != 1 else ''}, {quiz.lang_ask} -> {quiz.lang_answer}. "
                     "Type the translation. Send ? to skip a word, /cancel to stop.\n\n" + self._question(quiz))

    @staticmethod
    def _question(quiz) -> str:
        return f"{quiz.pos + 1}/{len(quiz.cards)}  {quiz.current.ask}"

    def _quiz_restart(self, uid: int, last, which: str) -> Reply:
        reverse = getattr(last, "reverse", False)
        if which == "missed":
            cards = list(last.missed)
            if not cards:
                return Reply("Nothing left to practise.")
            quiz = vocab.Quiz(last.set_id, last.title, last.lang_ask, last.lang_answer, cards, reverse=reverse)
            return self._quiz_begin(uid, quiz, reverse)
        vset = vocab.set_by_id(last.set_id, self.resolve(uid) or "")
        if vset is None:
            return Reply("That vocabulary page is gone. Send /quiz NUMBER for another.")
        return self._quiz_begin(uid, vocab.make_quiz(vset, last.title, reverse), reverse)   # a fresh weighted round

    SKIP = {"?", "skip", "pass", "idk", "weiss nicht", "weiß nicht"}

    def _quiz_answer(self, uid: int, text: str) -> Reply:
        with self._lock:
            s = self._s(uid)
            quiz = s.quiz
        if quiz is None or quiz.current is None:
            return Reply("No test is running. Send /quiz NUMBER to start one.")
        card = quiz.current
        if text.strip().lower() in self.SKIP:
            ok, line = False, f"The answer: {card.answer}"
        else:
            verdict = vocab.check_answer(text, card.answer)
            ok = verdict.kind in ("correct", "close")
            line = ("Correct." + (f" {verdict.note}" if verdict.note else "")) if ok else f"Not quite. It's: {card.answer}"
        vocab.record(card.id, ok)
        quiz.pos += 1
        if ok:
            quiz.correct += 1
        else:
            quiz.missed.append(card)
        if quiz.current is not None:
            vocab.save_quiz(uid, quiz)
            return Reply(f"{line}\n\n{self._question(quiz)}")
        with self._lock:                                     # finished
            s.quiz, s.last_quiz, s.state = None, quiz, None
        vocab.save_quiz(uid, quiz, running=False)
        total = len(quiz.cards)
        summary = f"{line}\n\nDone: {quiz.correct}/{total} right."
        buttons = []
        if quiz.missed:
            summary += "\n\nTo practise:\n" + "\n".join(f"{c.ask} = {c.answer}" for c in quiz.missed[:15])
            buttons.append([(f"Practise the {len(quiz.missed)} I missed", "quiz:missed")])
        buttons.append([("Test everything again", "quiz:again")])
        return Reply(summary, buttons)
