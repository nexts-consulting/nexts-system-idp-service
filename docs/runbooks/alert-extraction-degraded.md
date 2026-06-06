# Alert: ExtractionThroughputDegraded

## Meaning

`idp_extraction_tokens_per_second` &lt; 85% of 472.84 for 10 minutes.

## Checks

1. RunPod GPU utilization (DCGM or provider dashboard)
2. Batch size histogram — if consistently &lt; 16, increase `BATCH_MAX_WAIT_MS`
3. lmdeploy OOM or 5xx in extraction logs
4. Image size — preprocess `MAX_IMAGE_EDGE` may be too large

## Mitigation

- Reduce concurrent batches
- Restart lmdeploy pod
- Verify model weights loaded: `OpenGVLab/InternVL3_5-8B-Flash`
