# CMDBService has been removed.
# Application resolution is now handled by ApplicationResolutionService,
# which queries the applications table in the Enterprise Operations Database directly.
raise ImportError(
    "CMDBService has been removed. Use ApplicationResolutionService instead."
)
