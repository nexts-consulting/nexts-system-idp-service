from idp_contracts.compat import StrEnum


class JobStatus(StrEnum):
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    PREPROCESSING = "PREPROCESSING"
    FRAUD_DETECTED = "FRAUD_DETECTED"
    READY_FOR_EXTRACTION = "READY_FOR_EXTRACTION"
    BATCHING = "BATCHING"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    RULES_APPLIED = "RULES_APPLIED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobStage(StrEnum):
    DOWNLOAD = "download"
    PREPROCESS = "preprocess"
    FRAUD = "fraud"
    BATCH_WAIT = "batch_wait"
    EXTRACT = "extract"
    RULES = "rules"
    CALLBACK = "callback"


class CircuitBreakerState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"
