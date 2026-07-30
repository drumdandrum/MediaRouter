from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from typing import Iterator


def rollback_and_close(conn: sqlite3.Connection) -> None:
    """Best-effort cleanup when an open connection cannot transfer ownership."""
    try:
        conn.rollback()
    except BaseException:
        pass
    try:
        conn.close()
    except BaseException:
        pass


@contextmanager
def connection_scope(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Preserve sqlite's transaction context while guaranteeing closure."""
    try:
        with conn:
            yield conn
    except BaseException:
        try:
            conn.close()
        except BaseException:
            pass
        raise
    else:
        conn.close()
