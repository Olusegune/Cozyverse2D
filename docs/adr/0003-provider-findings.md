# ADR 0003: Provider API findings (docs checked 2026-10-23)

The kit asks for adapters verified against live docs. What was found, and what was decided.

| Provider | Finding | Decision |
|---|---|---|
| Google Gemini | Image output now uses `POST /v1beta/interactions` with `x-goog-api-key`, `input` blocks and `response_format {type:"image", aspect_ratio, image_size}`; result in `steps[].content[]` or `output_image.data`. Not the older `generateContent` shape. | Adapter follows the Interactions API. Five catalogue models, original Nano Banana marked deprecated. |
| OpenAI | Images API `POST /v1/images/generations` and `/edits` (multipart, `image[]`, `mask`). The guide names `gpt-image-2.5-sunburst` and `gpt-image-2.5-flare`; `gpt-image-2` is not named there. | `gpt-image-2` stays `unknown` with edit unsupported. Edit masks are converted: our masks are white where editing, OpenAI wants a same-size RGBA mask with transparent edit area. |
| Kie | `POST /api/v1/jobs/createTask`, poll `GET /api/v1/jobs/recordInfo?taskId=`, HTTP 200 even on error (use body `code`), results in `resultJson.resultUrls`. Only the slug `google/nano-banana` is confirmed. | Generate-only adapter for that slug; edit and Pro slugs registered but disabled until their fields are verified. Result URLs go through the SSRF-safe downloader. No cancel endpoint, so cancelling marks the task a possible orphan. |
| Meshy | Fully documented (`/openapi/v1/image-to-3d`, `model_urls.glb`, DELETE only refunds PENDING tasks). | Full adapter. The create call is never auto-retried because it bills. |
| Tripo | The task and upload request bodies could not be retrieved from the docs. | Registered but **disabled**: a wrong guess would spend credits. Enable once verified. |
| Vision | No verified model slug or JSON-output schema for detection. | The Google detector requires the user to enter a model slug; output is parsed defensively and every proposal starts unconfirmed. Marked experimental. |

Prices are never hardcoded. The user can enter a per-image or per-task price; otherwise the cost shows as unknown and needs explicit approval.

Live calls were not made because no keys were available. Everything is contract-tested against recorded request shapes with mock transports; no model is marked verified until a real request succeeds for the user.
