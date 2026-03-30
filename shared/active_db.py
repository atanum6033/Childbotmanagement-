"""
Auto-selects the correct database module (SQLite or MongoDB)
based on the current configuration at startup.

Import this as `db` everywhere:
    from shared import active_db as db
"""

from shared.db_config import get_db_type as _get_db_type

_SHARED_EXPORTS = [
    "get_db_path", "get_child_db_path", "DATA_DIR",
    "init_admin_db", "init_child_db",
    "is_admin", "is_owner", "add_admin", "remove_admin", "list_admins", "get_admin_ids",
    "add_child_bot", "remove_child_bot", "get_child_bot", "get_child_bot_by_token",
    "list_child_bots", "set_child_bot_running", "set_all_bots_stopped",
    "upsert_user", "get_user", "set_user_blocked", "set_user_inactive",
    "count_users", "get_active_users", "get_non_blocked_users",
    "get_all_users_export", "import_users_from_list", "get_all_users_paginated",
    "get_setting", "set_setting",
    "add_channel", "remove_channel", "toggle_channel_mandatory",
    "list_channels", "get_mandatory_channels",
    "log_broadcast",
    "is_child_admin", "add_child_admin", "remove_child_admin", "list_child_admins",
    "request_admin_access", "get_pending_requests", "resolve_request",
]

_db_type = _get_db_type()

if _db_type == "mongodb":
    try:
        from shared.mongo_db import (
            get_db_path, get_child_db_path, DATA_DIR,
            init_admin_db, init_child_db,
            is_admin, is_owner, add_admin, remove_admin, list_admins, get_admin_ids,
            add_child_bot, remove_child_bot, get_child_bot, get_child_bot_by_token,
            list_child_bots, set_child_bot_running, set_all_bots_stopped,
            upsert_user, get_user, set_user_blocked, set_user_inactive,
            count_users, get_active_users, get_non_blocked_users,
            get_all_users_export, import_users_from_list, get_all_users_paginated,
            get_setting, set_setting,
            add_channel, remove_channel, toggle_channel_mandatory,
            list_channels, get_mandatory_channels,
            log_broadcast,
            is_child_admin, add_child_admin, remove_child_admin, list_child_admins,
            request_admin_access, get_pending_requests, resolve_request,
        )
        import logging as _log
        _log.getLogger(__name__).info("active_db: using MongoDB.")
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).warning(f"MongoDB unavailable ({_e}), falling back to SQLite.")
        from shared.database import (
            get_db_path, get_child_db_path, DATA_DIR,
            init_admin_db, init_child_db,
            is_admin, is_owner, add_admin, remove_admin, list_admins, get_admin_ids,
            add_child_bot, remove_child_bot, get_child_bot, get_child_bot_by_token,
            list_child_bots, set_child_bot_running, set_all_bots_stopped,
            upsert_user, get_user, set_user_blocked, set_user_inactive,
            count_users, get_active_users, get_non_blocked_users,
            get_all_users_export, import_users_from_list, get_all_users_paginated,
            get_setting, set_setting,
            add_channel, remove_channel, toggle_channel_mandatory,
            list_channels, get_mandatory_channels,
            log_broadcast,
            is_child_admin, add_child_admin, remove_child_admin, list_child_admins,
            request_admin_access, get_pending_requests, resolve_request,
        )
else:
    from shared.database import (
        get_db_path, get_child_db_path, DATA_DIR,
        init_admin_db, init_child_db,
        is_admin, is_owner, add_admin, remove_admin, list_admins, get_admin_ids,
        add_child_bot, remove_child_bot, get_child_bot, get_child_bot_by_token,
        list_child_bots, set_child_bot_running, set_all_bots_stopped,
        upsert_user, get_user, set_user_blocked, set_user_inactive,
        count_users, get_active_users, get_non_blocked_users,
        get_all_users_export, import_users_from_list, get_all_users_paginated,
        get_setting, set_setting,
        add_channel, remove_channel, toggle_channel_mandatory,
        list_channels, get_mandatory_channels,
        log_broadcast,
        is_child_admin, add_child_admin, remove_child_admin, list_child_admins,
        request_admin_access, get_pending_requests, resolve_request,
    )
    import logging as _log
    _log.getLogger(__name__).info("active_db: using SQLite.")
