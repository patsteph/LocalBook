"""PlansMixin — extracted from the former services/curator_brain.py (Wave 4 split)."""
from ._models import *  # noqa: F401,F403


class PlansMixin:
    def create_plan(
        self,
        intent: str,
        summary: str,
        steps: List[Dict[str, str]],
        notebook_id: Optional[str] = None,
        user_visible: bool = True,
    ) -> Optional[str]:
        """Create a new plan + its steps. Returns plan_id (uuid) or None."""
        import uuid as _uuid
        plan_id = str(_uuid.uuid4())
        now = datetime.utcnow().isoformat()
        try:
            self._conn.execute(
                """INSERT INTO plans
                   (plan_id, notebook_id, intent, summary, status, created_at, user_visible)
                   VALUES (?, ?, ?, ?, 'proposed', ?, ?)""",
                (plan_id, notebook_id, intent, summary, now, 1 if user_visible else 0),
            )
            for i, step in enumerate(steps, start=1):
                self._conn.execute(
                    """INSERT INTO plan_steps
                       (plan_id, seq, name, description, status)
                       VALUES (?, ?, ?, ?, 'pending')""",
                    (plan_id, i, step.get("name", f"step_{i}"), step.get("description")),
                )
            self._conn.commit()
            self._emit_plan_event("plan_created", plan_id, notebook_id, intent=intent)
            return plan_id
        except Exception as e:
            logger.warning(f"[CuratorBrain] create_plan failed: {e}")
            return None

    def start_plan(self, plan_id: str) -> bool:
        try:
            now = datetime.utcnow().isoformat()
            self._conn.execute(
                "UPDATE plans SET status = 'running', started_at = ? WHERE plan_id = ? AND status = 'proposed'",
                (now, plan_id),
            )
            self._conn.commit()
            self._emit_plan_event("plan_started", plan_id, None)
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] start_plan failed: {e}")
            return False

    def start_step(self, plan_id: str, seq: int) -> bool:
        try:
            now = datetime.utcnow().isoformat()
            self._conn.execute(
                """UPDATE plan_steps
                   SET status = 'running', started_at = ?
                   WHERE plan_id = ? AND seq = ? AND status = 'pending'""",
                (now, plan_id, seq),
            )
            self._conn.commit()
            self._emit_plan_event("plan_step_started", plan_id, None,
                                  payload={"seq": seq})
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] start_step failed: {e}")
            return False

    def complete_step(
        self,
        plan_id: str,
        seq: int,
        output_summary: Optional[str] = None,
    ) -> bool:
        try:
            now = datetime.utcnow().isoformat()
            self._conn.execute(
                """UPDATE plan_steps
                   SET status = 'done', finished_at = ?, output_summary = ?
                   WHERE plan_id = ? AND seq = ?""",
                (now, output_summary, plan_id, seq),
            )
            # Auto-complete the plan if all steps are done.
            row = self._conn.execute(
                """SELECT
                     SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done_n,
                     COUNT(*) AS total_n
                   FROM plan_steps WHERE plan_id = ?""",
                (plan_id,),
            ).fetchone()
            self._conn.commit()
            self._emit_plan_event("plan_step_completed", plan_id, None,
                                  payload={"seq": seq, "output_summary": output_summary})
            if row and row["done_n"] == row["total_n"] and row["total_n"] > 0:
                self._conn.execute(
                    """UPDATE plans
                       SET status = 'completed', finished_at = ?
                       WHERE plan_id = ?""",
                    (now, plan_id),
                )
                self._conn.commit()
                self._emit_plan_event("plan_completed", plan_id, None)
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] complete_step failed: {e}")
            return False

    def fail_step(self, plan_id: str, seq: int, reason: str) -> bool:
        try:
            now = datetime.utcnow().isoformat()
            self._conn.execute(
                """UPDATE plan_steps
                   SET status = 'failed', finished_at = ?, output_summary = ?
                   WHERE plan_id = ? AND seq = ?""",
                (now, reason, plan_id, seq),
            )
            self._conn.execute(
                """UPDATE plans
                   SET status = 'failed', finished_at = ?
                   WHERE plan_id = ?""",
                (now, plan_id),
            )
            self._conn.commit()
            self._emit_plan_event("plan_failed", plan_id, None,
                                  payload={"seq": seq, "reason": reason})
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] fail_step failed: {e}")
            return False

    def cancel_plan(self, plan_id: str, reason: str = "") -> bool:
        try:
            now = datetime.utcnow().isoformat()
            self._conn.execute(
                """UPDATE plans
                   SET status = 'cancelled', finished_at = ?
                   WHERE plan_id = ?""",
                (now, plan_id),
            )
            self._conn.commit()
            self._emit_plan_event("plan_cancelled", plan_id, None,
                                  payload={"reason": reason})
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] cancel_plan failed: {e}")
            return False

    def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        try:
            plan_row = self._conn.execute(
                "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if not plan_row:
                return None
            steps_rows = self._conn.execute(
                "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY seq",
                (plan_id,),
            ).fetchall()
            return {
                "plan_id": plan_row["plan_id"],
                "notebook_id": plan_row["notebook_id"],
                "intent": plan_row["intent"],
                "summary": plan_row["summary"],
                "status": plan_row["status"],
                "created_at": plan_row["created_at"],
                "started_at": plan_row["started_at"],
                "finished_at": plan_row["finished_at"],
                "user_visible": bool(plan_row["user_visible"]),
                "steps": [
                    {
                        "seq": s["seq"],
                        "name": s["name"],
                        "description": s["description"],
                        "status": s["status"],
                        "started_at": s["started_at"],
                        "finished_at": s["finished_at"],
                        "output_summary": s["output_summary"],
                    }
                    for s in steps_rows
                ],
            }
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_plan failed: {e}")
            return None

    def recent_plans(
        self,
        limit: int = 20,
        notebook_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            clauses: List[str] = []
            params: List[Any] = []
            if notebook_id:
                clauses.append("notebook_id = ?")
                params.append(notebook_id)
            if status:
                clauses.append("status = ?")
                params.append(status)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            cur = self._conn.execute(
                f"""SELECT plan_id, notebook_id, intent, summary, status,
                           created_at, started_at, finished_at, user_visible
                    FROM plans{where}
                    ORDER BY created_at DESC
                    LIMIT ?""",
                params,
            )
            return [
                {
                    "plan_id": r["plan_id"],
                    "notebook_id": r["notebook_id"],
                    "intent": r["intent"],
                    "summary": r["summary"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "started_at": r["started_at"],
                    "finished_at": r["finished_at"],
                    "user_visible": bool(r["user_visible"]),
                }
                for r in cur.fetchall()
            ]
        except Exception as e:
            logger.warning(f"[CuratorBrain] recent_plans failed: {e}")
            return []

    def _emit_plan_event(
        self,
        action: str,
        plan_id: str,
        notebook_id: Optional[str],
        payload: Optional[Dict[str, Any]] = None,
        intent: Optional[str] = None,
    ) -> None:
        """Fire a plan_* event onto the curator event bus.

        If notebook_id is None, looks it up from the plans table so
        downstream consumers (Phase 5 plan_completed overwatch trigger)
        can route by notebook.
        """
        try:
            # Look up notebook_id when caller didn't supply it — costs
            # one cheap indexed query.
            if notebook_id is None:
                try:
                    row = self._conn.execute(
                        "SELECT notebook_id FROM plans WHERE plan_id = ?",
                        (plan_id,),
                    ).fetchone()
                    if row:
                        notebook_id = row["notebook_id"]
                except Exception:
                    pass
            from services.curator_event_bus import event_bus
            base_payload: Dict[str, Any] = {"plan_id": plan_id}
            if payload:
                base_payload.update(payload)
            event_bus.emit_now(
                actor="@curator",
                action=action,
                notebook_id=notebook_id,
                intent=intent,
                payload=base_payload,
                outcome="success",
            )
        except Exception:
            # Plan ledger is observability — never propagate failures.
            pass

    def register_cancellable(self, plan_id: str) -> None:
        """Register a plan as cancellable. Idempotent."""
        if not hasattr(self, "_cancellation_events"):
            self._cancellation_events: Dict[str, "asyncio.Event"] = {}
        if plan_id not in self._cancellation_events:
            import asyncio as _asyncio
            self._cancellation_events[plan_id] = _asyncio.Event()

    def is_cancelled(self, plan_id: str) -> bool:
        """Check whether a cancel signal has been issued for this plan."""
        evt = getattr(self, "_cancellation_events", {}).get(plan_id)
        return bool(evt and evt.is_set())

    def trigger_cancel(self, plan_id: str) -> bool:
        """Signal cancellation for plan_id. Returns True if the plan was
        registered as cancellable (i.e. it was running), False otherwise.

        Called by the HTTP cancel endpoint when the user clicks Stop.
        Safe to call repeatedly.
        """
        evt = getattr(self, "_cancellation_events", {}).get(plan_id)
        if evt is None:
            return False
        evt.set()
        return True

    def unregister_cancellable(self, plan_id: str) -> None:
        """Clean up the registry entry. Called in finally after the
        plan reaches a terminal state.
        """
        evts = getattr(self, "_cancellation_events", None)
        if evts is not None:
            evts.pop(plan_id, None)
