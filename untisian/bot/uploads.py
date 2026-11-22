"""Photos in (Kurs and kind questions, saving), and /list, /search, /show, /delete, /retry."""
import logging

from untisian import ocr
from untisian.bot.common import MAX_PENDING, MAX_PENDING_BYTES, Reply, _one_line, _rows
from untisian.core.materials import (KINDS, KURS_RE, UploadError, delete_material, list_materials, material_text,
                                     recent_kurse, request_reread, save_upload, search_materials)

log = logging.getLogger(__name__)



class UploadCommands:
    def _suggestions(self, uid: int, limit: int = 12) -> list[str]:
        owner = self.resolve(uid) or ""
        names = recent_kurse(owner)
        try:
            extra = self.kurs_source(owner)
        except Exception:
            log.exception("could not load Kurs suggestions")
            extra = []
        names += [k for k in extra if k not in names and KURS_RE.match(k)]
        return names[:limit]

    def _ask_kurs(self, uid: int) -> Reply:
        s = self._s(uid)
        s.state = "kurs"
        n = len(s.pending)
        rows = _rows(self._suggestions(uid))
        hint = self.t(uid, "hint_tap" if rows else "hint_type")
        return Reply(self.t(uid, "ask_kurs", n=n, s="s" if n != 1 else "", hint=hint), rows)

    def on_files(self, uid: int, items: list[tuple[bytes, str]]) -> Reply:
        with self._lock:
            s = self._s(uid)
            if s.state and s.state.startswith("login"):
                return self.r(uid, "login_busy_photos")
            if s.state == "quiz":
                return self.r(uid, "quiz_busy_photos")
            held = sum(len(b) for b, _ in s.pending)
            take = []
            for data, name in items:
                if len(s.pending) + len(take) >= MAX_PENDING or held + len(data) > MAX_PENDING_BYTES:
                    break
                take.append((data, name))
                held += len(data)
            s.pending.extend(take)
            skipped = len(items) - len(take)
            note = self.t(uid, "left_out", n=skipped, max=MAX_PENDING) if skipped else ""
            if not s.pending:
                self.sessions.pop(uid, None)
                return self.r(uid, "too_much")
            if s.state is None:
                reply = self._ask_kurs(uid)
                return Reply(reply.text + note, reply.buttons)
            return self.r(uid, "added", n=len(s.pending), note=note)

    def _ask_kind(self, uid: int, kurs: str) -> Reply:
        s = self._s(uid)
        s.kurs, s.state = kurs, "kind"
        return Reply(self.t(uid, "ask_kind", kurs=kurs), [[(self.t(uid, f"kind_{k}"), f"kind:{k}")] for k in sorted(KINDS)])

    def _save(self, uid: int, kind: str) -> Reply:
        with self._lock:                                   # take the photos out, then do the slow work unlocked
            s = self._s(uid)
            kurs, items = s.kurs, s.pending
            self.sessions.pop(uid, None)
        owner = self.resolve(uid)
        if owner is None:
            return self.refuse(uid)
        saved, dup, errors = [], 0, []
        for data, name in items:
            try:
                m = save_upload(data, name, kurs, kind, owner=owner)
                if m.duplicate:
                    dup += 1
                else:
                    saved.append(m.id)
            except UploadError as e:
                errors.append(f"{name}: {self.err(uid, e)}")
        if len(self.from_telegram) > 1000:                  # never grow without bound
            self.from_telegram.clear()
        self.from_telegram.update(saved)
        parts = []
        if saved:
            parts.append(self.t(uid, "saved", n=len(saved), kurs=kurs, kind=self.t(uid, f"kind_{kind}"),
                                ids="#" + ", #".join(map(str, saved))))
        if dup:
            parts.append(self.t(uid, "dup", n=dup))
        if errors:
            parts.append(self.t(uid, "problems", list="\n".join(errors)))
        if saved and kind == "vocab":
            parts.append(self.t(uid, "test_yourself", id=saved[0]))
        if saved and ocr.enabled():
            parts.append(self.t(uid, "reading_bg"))
        return Reply(" ".join(parts) if parts else self.t(uid, "nothing_to_save"))

    # --- your materials -----------------------------------------------------------------------

    def list(self, uid: int, *args: str) -> Reply:
        ms = list_materials(owner=self.resolve(uid) or "", limit=10)
        if not ms:
            return self.r(uid, "no_uploads")
        return Reply("\n".join(f"#{m.id}  {m.kurs}  {m.kind}  {m.created[:10]}  {m.title or m.filename}" for m in ms))

    def search(self, uid: int, *args: str) -> Reply:
        query = " ".join(args).strip()
        if not query:
            return self.r(uid, "search_what")
        found = search_materials(query, owner=self.resolve(uid) or "", limit=8)
        if not found:
            note = "" if ocr.enabled() else self.t(uid, "search_off_note")
            return self.r(uid, "search_none", query=query, note=note)
        return Reply("\n\n".join(f"#{m.id}  {m.kind}  {m.kurs}\n  {_one_line(m.snippet or m.title or m.filename, 110)}"
                                 for m in found) + self.t(uid, "show_hint"))

    @staticmethod
    def _id(args: tuple[str, ...]) -> int | None:
        return int(args[0].lstrip("#")) if args and args[0].lstrip("#").isdecimal() else None

    def show(self, uid: int, *args: str) -> Reply:
        mid = self._id(args)
        if mid is None:
            return self.r(uid, "which_show")
        got = material_text(mid, self.resolve(uid) or "")
        if got is None:
            return self.r(uid, "no_upload")
        m, text = got
        head = f"#{m.id}  {m.kind}  {m.kurs}  {m.created[:10]}  {m.title or m.filename}"
        if text:
            return Reply(f"{head}\n\n{text}")
        key = {"pending": "still_reading", "failed": "read_failed", "off": "reading_off"}.get(m.ocr_status, "no_text")
        status = self.t(uid, key, id=m.id)
        return Reply(f"{head}\n\n{status}")

    def delete(self, uid: int, *args: str) -> Reply:
        mid = self._id(args)
        if mid is None:
            return self.r(uid, "which_delete")
        if not delete_material(mid, self.resolve(uid) or ""):
            return self.r(uid, "no_upload")
        self.from_telegram.discard(mid)                     # ids get reused, so forget it
        return self.r(uid, "deleted", id=mid)

    def retry(self, uid: int, *args: str) -> Reply:
        mid = self._id(args)
        if mid is None:
            return self.r(uid, "which_retry")
        if request_reread(mid, self.resolve(uid) or ""):
            self.from_telegram.add(mid)
            return self.r(uid, "rereading", id=mid)
        return self.r(uid, "retry_failed")

    def on_read(self, uid: int, mid: int, status: str, text: str) -> str:
        """The message sent to `uid` when a photo finishes being read."""
        if status != "done":
            return self.t(uid, "read_fail_msg", id=mid)
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if not first:
            return self.t(uid, "read_empty", id=mid)
        return self.t(uid, "read_done", id=mid, first=_one_line(first, 120))
