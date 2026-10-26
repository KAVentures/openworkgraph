"""Opt-in company sign-in restricted to analysis permissions."""
from .portal_signin import PortalSessionStore, build_portal_signin_router


class AnalysisSessionStore(PortalSessionStore):
    def __init__(self,db,settings,*,now=None):
        super().__init__(db,settings,portal='analysis',now=now)


def build_analysis_signin_router(app,store,settings,delegated_human):
    return build_portal_signin_router(app,store,settings,delegated_human)
