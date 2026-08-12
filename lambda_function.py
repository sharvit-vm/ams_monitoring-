"""ZIP-based AWS Lambda entry point.

This keeps compatibility with SAM/templates that expect
`lambda_function.lambda_handler`.
"""

from lambda_handler import handler as lambda_handler
