from . import presentation as _presentation
from .first_name_policy import install as _install_first_name_policy
from .mail_row_policy import install as _install_mail_row_policy

_install_first_name_policy(_presentation)
_install_mail_row_policy(_presentation)

from . import analytics as _analytics
from .browser_context_policy import install as _install_browser_context_policy

_install_browser_context_policy(_analytics)
