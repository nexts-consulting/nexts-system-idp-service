import time

from idp_contracts.enums import CircuitBreakerState


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        open_seconds: int = 30,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self.state = CircuitBreakerState.CLOSED
        self.failures = 0
        self.opened_at: float | None = None

    def record_success(self) -> None:
        self.failures = 0
        self.state = CircuitBreakerState.CLOSED
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            self.opened_at = time.time()

    def allow_request(self) -> bool:
        if self.state == CircuitBreakerState.CLOSED:
            return True
        if self.state == CircuitBreakerState.OPEN:
            if self.opened_at and time.time() - self.opened_at >= self.open_seconds:
                self.state = CircuitBreakerState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN

    def state_value(self) -> int:
        return {"CLOSED": 0, "OPEN": 1, "HALF_OPEN": 2}[self.state.value]
