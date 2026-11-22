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
                return self.r(uid, "vocab_first")
            return latest
        got = material_text(mid, owner)
        if got is None:
            return self.r(uid, "no_upload")
        if vocab.set_for_material(mid, owner) is None and not accounts.action_allowed("vocab", uid):
            return self.r(uid, "vocab_rate")
        try:
            return vocab.build_set(mid, owner)
        except vocab.VocabError as e:
            return Reply(self.err(uid, e))

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
        head = self.t(uid, "words_head", title=self._title_of(vset, owner), n=len(rows),
                      a=vset["lang_front"] or "?", b=vset["lang_back"] or "?")
        return Reply(head + "\n\n" + "\n".join(f"{r['front']} = {r['back']}" for r in rows))

    def quiz(self, uid: int, *args: str) -> Reply:
        owner = self.resolve(uid)
        if owner is None:
            return self.refuse(uid)
        mid, reverse = self._quiz_args(args)
        with self._lock:
            s = self._s(uid)
            if s.pending:
                return self.r(uid, "photos_waiting_quiz")
            if s.state:
                return self.r(uid, "busy")
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
        head = self.t(uid, "quiz_head", title=quiz.title, n=n, words=self._words(uid, n), a=quiz.lang_ask, b=quiz.lang_answer)
        return Reply(head + "\n\n" + self._question(quiz))

    def _words(self, uid: int, n: int) -> str:
        return self.t(uid, "word_one" if n == 1 else "word_many")

    @staticmethod
    def _question(quiz) -> str:
        return f"{quiz.pos + 1}/{len(quiz.cards)}  {quiz.current.ask}"

    def _quiz_restart(self, uid: int, last, which: str) -> Reply:
        reverse = getattr(last, "reverse", False)
        if which == "missed":
            cards = list(last.missed)
            if not cards:
                return self.r(uid, "nothing_left")
            quiz = vocab.Quiz(last.set_id, last.title, last.lang_ask, last.lang_answer, cards, reverse=reverse)
            return self._quiz_begin(uid, quiz, reverse)
        vset = vocab.set_by_id(last.set_id, self.resolve(uid) or "")
        if vset is None:
            return self.r(uid, "page_gone")
        return self._quiz_begin(uid, vocab.make_quiz(vset, last.title, reverse), reverse)   # a fresh weighted round

    SKIP = {"?", "skip", "pass", "idk", "weiss nicht", "weiß nicht"}
    # Typing one of these during a test ends it (unless it is the answer to the current word).
    STOP = {"stop", "stopp", "stop it", "stop the test", "stop test", "end", "end test", "end the test", "quit", "exit",
            "cancel", "abbrechen", "abbruch", "beenden", "test beenden", "test abbrechen", "aufhören", "aufhoeren",
            "ende", "schluss", "/stop", "parar", "basta", "fin"}

    def _quiz_answer(self, uid: int, text: str) -> Reply:
        with self._lock:
            s = self._s(uid)
            quiz = s.quiz
        if quiz is None or quiz.current is None:
            return self.r(uid, "no_test")
        card = quiz.current
        said = " ".join(text.strip().lower().rstrip("!.").split())
        if said in self.STOP and vocab.check_answer(text, card.answer).kind == "wrong":
            return self._quiz_stop(uid, quiz)
        if said in self.SKIP:
            ok, line = False, self.t(uid, "skip_answer", answer=card.answer)
        else:
            verdict = vocab.check_answer(text, card.answer)
            ok = verdict.kind in ("correct", "close")
            note = self.t(uid, f"note_{verdict.reason}", form=verdict.form) if verdict.reason else ""
            line = (self.t(uid, "correct") + (f" {note}" if note else "")) if ok else self.t(uid, "wrong", answer=card.answer)
        vocab.record(card.id, ok)
        quiz.pos += 1
        if ok:
            quiz.correct += 1
        else:
            quiz.missed.append(card)
        if quiz.current is not None:
            vocab.save_quiz(uid, quiz)
            return Reply(f"{line}\n\n{self._question(quiz)}")
        return self._quiz_end(uid, quiz, f"{line}\n\n" + self.t(uid, "done", c=quiz.correct, t=len(quiz.cards)))

    def _quiz_stop(self, uid: int, quiz) -> Reply:
        """End a test early, with the score for the words asked so far."""
        asked = quiz.pos
        quiz.cards = quiz.cards[:asked]
        if not asked:
            with self._lock:
                s = self._s(uid)
                s.quiz, s.state = None, None
            vocab.save_quiz(uid, None)
            return self.r(uid, "test_stopped")
        return self._quiz_end(uid, quiz, self.t(uid, "stopped_after", n=asked, words=self._words(uid, asked), c=quiz.correct))

    def _quiz_end(self, uid: int, quiz, summary: str) -> Reply:
        with self._lock:
            s = self._s(uid)
            s.quiz, s.last_quiz, s.state = None, quiz, None
        vocab.save_quiz(uid, quiz, running=False)
        buttons = []
        if quiz.missed:
            summary += f"\n\n{self.t(uid, 'to_practise')}\n" + "\n".join(f"{c.ask} = {c.answer}" for c in quiz.missed[:15])
            buttons.append([(self.t(uid, "btn_missed", n=len(quiz.missed)), "quiz:missed")])
        buttons.append([(self.t(uid, "btn_again"), "quiz:again")])
        return Reply(summary, buttons)
