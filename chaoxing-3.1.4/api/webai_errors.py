"""Errors shared by the answer client and image transport."""


class WebAIError(ValueError):
    """A bridge, configuration, image, or answer validation failure."""


class InvalidAnswerError(WebAIError):
    """Generation completed, but bounded retries could not validate its answer."""


class GenerationError(WebAIError):
    """An upstream generation failed; another configured model may be tried."""


class RateLimitedError(WebAIError):
    """The upstream requires a pause, not an immediate model fallback."""


class VisionUnavailableError(WebAIError):
    """No configured, image-capable upstream is available."""


class QuestionImageUnavailable(WebAIError):
    """平台那张题图取不回来（404/502/不是图片），这道题没法读，跳过它不要瞎猜。"""


class VisionReviewError(InvalidAnswerError):
    """An image reading or independent answer check requires manual review."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)
