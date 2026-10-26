"""Opt-in company sign-in restricted to process permissions."""
from .portal_signin import PortalSessionStore, build_portal_signin_router


class ProcessSessionStore(PortalSessionStore):
    def __init__(self,db,settings,*,now=None):
        super().__init__(db,settings,portal='process',now=now)


def build_process_signin_router(app,store,settings,delegated_human):
    return build_portal_signin_router(app,store,settings,delegated_human)
