"""HealthMixin — extracted from the former agents/collector.py (Wave 6 split)."""
from ._models import *  # noqa: F401,F403


class HealthMixin:
    def update_source_health(
        self,
        source_id: str,
        source_url: str,
        success: bool,
        response_time_ms: float = 0,
        items_found: int = 0
    ) -> SourceHealthRecord:
        """Update health tracking for a source"""
        if source_id not in self._source_health:
            self._source_health[source_id] = SourceHealthRecord(
                source_id=source_id,
                source_url=source_url
            )
        
        health = self._source_health[source_id]
        
        if success:
            health.last_success = datetime.utcnow()
            health.failure_count = 0
            health.items_collected += items_found
            
            # Update average response time
            if health.avg_response_time_ms > 0:
                health.avg_response_time_ms = (health.avg_response_time_ms + response_time_ms) / 2
            else:
                health.avg_response_time_ms = response_time_ms
            
            # Determine health status
            if response_time_ms > 5000:
                health.health = SourceHealth.DEGRADED
            else:
                health.health = SourceHealth.HEALTHY
        else:
            health.last_failure = datetime.utcnow()
            health.failure_count += 1
            
            # Escalate health status based on failure count
            if health.failure_count >= 5:
                health.health = SourceHealth.DEAD
            elif health.failure_count >= 3:
                health.health = SourceHealth.FAILING
            else:
                health.health = SourceHealth.DEGRADED
        
        return health

    def get_source_health_report(self) -> List[Dict[str, Any]]:
        """Get health report for all sources"""
        return [
            {
                "source_id": h.source_id,
                "url": h.source_url,
                "health": h.health.value,
                "failure_count": h.failure_count,
                "items_collected": h.items_collected,
                "avg_response_ms": h.avg_response_time_ms
            }
            for h in self._source_health.values()
        ]
