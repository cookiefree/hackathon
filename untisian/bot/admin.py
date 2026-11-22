"""Admins only: /invite, /invites, /revoke."""
from untisian.bot.common import Reply
from untisian.core import accounts


class AdminCommands:
    def invite(self, uid: int, *args: str) -> Reply:
        denied = self._admin_only(uid)
        if denied:
            return denied
        uses = min(int(args[0]), 200) if args and args[0].isdecimal() and int(args[0]) > 0 else accounts.INVITE_USES
        code = accounts.create_invite(uses)
        link = f"https://t.me/{self.bot_username}?start={code}" if self.bot_username else f"/start {code}"
        return self.r(uid, "invite_made", uses=uses, days=accounts.INVITE_DAYS, link=link, code=code)

    def invites(self, uid: int, *args: str) -> Reply:
        denied = self._admin_only(uid)
        if denied:
            return denied
        rows = accounts.list_invites()
        if not rows:
            return self.r(uid, "no_invites")
        return Reply("\n".join(self.t(uid, "invite_row", code=r["code"], uses=r["uses"], max=r["max_uses"],
                                      state=self.t(uid, "invite_active" if r["active"] else "invite_off"),
                                      until=r["expires"][:10]) for r in rows))

    def revoke(self, uid: int, *args: str) -> Reply:
        denied = self._admin_only(uid)
        if denied:
            return denied
        if not args:
            return self.r(uid, "revoke_which")
        return self.r(uid, "revoked" if accounts.revoke_invite(args[0]) else "no_invite")
