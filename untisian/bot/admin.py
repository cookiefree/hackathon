"""Admins only: /invite, /invites, /revoke."""
from untisian.bot.common import Reply
from untisian.core import accounts



class AdminCommands:
    # --- admin: invites -----------------------------------------------------------------------

    def invite(self, uid: int, *args: str) -> Reply:
        denied = self._admin_only(uid)
        if denied:
            return denied
        uses = min(int(args[0]), 200) if args and args[0].isdecimal() and int(args[0]) > 0 else accounts.INVITE_USES
        code = accounts.create_invite(uses)
        link = f"https://t.me/{self.bot_username}?start={code}" if self.bot_username else f"/start {code}"
        return Reply(f"Invite for up to {uses} people, valid {accounts.INVITE_DAYS} days:\n{link}\n\n"
                     f"Send it to your classmates. Turn it off any time with /revoke {code}")

    def invites(self, uid: int, *args: str) -> Reply:
        denied = self._admin_only(uid)
        if denied:
            return denied
        rows = accounts.list_invites()
        if not rows:
            return Reply("No invites yet. Create one with /invite")
        return Reply("\n".join(f"{r['code']}  {r['uses']}/{r['max_uses']} used  "
                               f"{'active' if r['active'] else 'off'}  until {r['expires'][:10]}" for r in rows))

    def revoke(self, uid: int, *args: str) -> Reply:
        denied = self._admin_only(uid)
        if denied:
            return denied
        if not args:
            return Reply("Which one? /revoke CODE (see /invites)")
        return Reply("Turned off." if accounts.revoke_invite(args[0]) else "No invite with that code.")
