# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from .views.base_view import BaseTableView
    from .views.community_settings_view import CommunitySettingsView
    from .views.vector_db_view import VectorDBView

    AnyDBView: TypeAlias = (
        BaseTableView | CommunitySettingsView | VectorDBView
    )
else:
    AnyDBView = object
