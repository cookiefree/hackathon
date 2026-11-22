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
        hint = "Tap one or type it" if rows else "Type it"
        return Reply(f"Got {n} photo{'s' if n != 1 else ''}. Which Kurs? {hint}, like E5-GK3.", rows)

    def on_files(self, uid: int, items: list[tuple[bytes, str]]) -> Reply:
        with self._lock:
            s = self._s(uid)
            if s.state and s.state.startswith("login"):
                return Reply("You're in the middle of logging in. Finish it, or send /cancel, before sending photos.")
            if s.state == "quiz":
                return Reply("You're in the middle of a vocabulary test. Finish it, or send /cancel, before sending photos.")
            held = sum(len(b) for b, _ in s.pending)
            take = []
            for data, name in items:
                if len(s.pending) + len(take) >= MAX_PENDING or held + len(data) > MAX_PENDING_BYTES:
                    break
                take.append((data, name))
                held += len(data)
            s.pending.extend(take)
            skipped = len(items) - len(take)
            note = f" ({skipped} left out: at most {MAX_PENDING} photos at a time)" if skipped else ""
            if not s.pending:
                self.sessions.pop(uid, None)
                return Reply("Too much at once. Send fewer or smaller photos.")
            if s.state is None:
                reply = self._ask_kurs(uid)
                return Reply(reply.text + note, reply.buttons)
            return Reply(f"Added. {len(s.pending)} photos waiting.{note}")

    def _ask_kind(self, uid: int, kurs: str) -> Reply:
        s = self._s(uid)
        s.kurs, s.state = kurs, "kind"
        return Reply(f"{kurs}. What kind of material?", [[(k, f"kind:{k}")] for k in sorted(KINDS)])

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
                errors.append(f"{name}: {e}")
        if len(self.from_telegram) > 1000:                  # never grow without bound
            self.from_telegram.clear()
        self.from_telegram.update(saved)
        parts = []
        if saved:
            parts.append(f"Saved {len(saved)} to {kurs} as {kind} (#{', #'.join(map(str, saved))}).")
        if dup:
            parts.append(f"{dup} already uploaded.")
        if errors:
            parts.append("Problems:\n" + "\n".join(errors))
        if saved and kind == "vocab":
            parts.append(f"Test yourself with /quiz {saved[0]}.")
        if saved and ocr.enabled():
            parts.append("I'm reading the text in the background and will tell you when it's done.")
        return Reply(" ".join(parts) if parts else "Nothing to save.")

    # --- your materials -----------------------------------------------------------------------

    def list(self, uid: int, *args: str) -> Reply:
        ms = list_materials(owner=self.resolve(uid) or "", limit=10)
        if not ms:
            return Reply("You haven't uploaded anything yet. Send me a photo.")
        return Reply("\n".join(f"#{m.id}  {m.kurs}  {m.kind}  {m.created[:10]}  {m.title or m.filename}" for m in ms))

    def search(self, uid: int, *args: str) -> Reply:
        query = " ".join(args).strip()
        if not query:
            return Reply("Tell me what to look for, e.g. /search ableitung")
        found = search_materials(query, owner=self.resolve(uid) or "", limit=8)
        if not found:
            note = "" if ocr.enabled() else " (Reading photos is switched off, so only titles are searchable.)"
            return Reply(f"Nothing found for '{query}'.{note}")
        return Reply("\n\n".join(f"#{m.id}  {m.kind}  {m.kurs}\n  {_one_line(m.snippet or m.title or m.filename, 110)}"
                                 for m in found) + "\n\n/show NUMBER for the full text")

    @staticmethod
    def _id(args: tuple[str, ...]) -> int | None:
        return int(args[0].lstrip("#")) if args and args[0].lstrip("#").isdecimal() else None

    def show(self, uid: int, *args: str) -> Reply:
        mid = self._id(args)
        if mid is None:
            return Reply("Which one? Use the number, e.g. /show 12 (see /list).")
        got = material_text(mid, self.resolve(uid) or "")
        if got is None:
            return Reply("No upload with that number.")
        m, text = got
        head = f"#{m.id}  {m.kind}  {m.kurs}  {m.created[:10]}  {m.title or m.filename}"
        if text:
            return Reply(f"{head}\n\n{text}")
        status = {"pending": "I'm still reading it.", "failed": f"Reading failed. Try /retry {m.id}.",
                  "off": "Reading photos is switched off."}.get(m.ocr_status, "No text was found in it.")
        return Reply(f"{head}\n\n{status}")

    def delete(self, uid: int, *args: str) -> Reply:
        mid = self._id(args)
        if mid is None:
            return Reply("Which one? Use the number, e.g. /delete 12 (see /list).")
        if not delete_material(mid, self.resolve(uid) or ""):
            return Reply("No upload with that number.")
        self.from_telegram.discard(mid)                     # ids get reused, so forget it
        return Reply(f"Deleted #{mid}.")

    def retry(self, uid: int, *args: str) -> Reply:
        mid = self._id(args)
        if mid is None:
            return Reply("Which one? Use the number, e.g. /retry 12.")
        if request_reread(mid, self.resolve(uid) or ""):
            self.from_telegram.add(mid)
            return Reply(f"Reading #{mid} again. I'll tell you when it's done.")
        return Reply("Couldn't do that: no such upload, it's already being read, or reading photos is switched off.")

    def on_read(self, mid: int, status: str, text: str) -> str:
        """The message sent when a photo finishes being read."""
        if status != "done":
            return f"#{mid} could not be read. Try /retry {mid}."
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if not first:
            return f"#{mid} was read, but I found no text in it."
        return f"#{mid} is read. It starts with: {_one_line(first, 120)}\n/show {mid} for the full text."
