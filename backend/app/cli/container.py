from __future__ import annotations


def build_container():
    from ..core.containers import Container
    from ..core.database import init_db

    container = Container()
    init_db(container.db())
    return container
