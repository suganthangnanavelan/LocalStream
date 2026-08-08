"""
metadata/ — M4: TMDB matching, poster/backdrop/synopsis/genre/rating/
language fetch + local caching, offline fallback art, anime-movie
classification (Section 4b).

Public surface:
  - tmdb_client: TmdbClient — search + details against the TMDB v3 API
  - matcher: best_match — fuzzy-picks the right TMDB candidate for a
    locally-parsed title
  - classifier: is_anime_movie — Section 4b's genre/language/keyword rule
  - image_cache: download_image / generate_fallback_art — local art cache
  - metadata_store: MetadataStore — "cached locally forever" persistence
  - enricher: MetadataEnricher — orchestrates the above over an M3 Library
"""

from metadata.classifier import is_anime_movie
from metadata.enricher import EnricherConfig, MetadataEnricher
from metadata.matcher import MatchResult, best_match
from metadata.metadata_store import CachedEpisodeMetadata, CachedTitleMetadata, MetadataStore
from metadata.tmdb_client import TmdbCandidate, TmdbClient, TmdbDetails, TmdbError

__all__ = [
    "is_anime_movie",
    "EnricherConfig",
    "MetadataEnricher",
    "MatchResult",
    "best_match",
    "CachedEpisodeMetadata",
    "CachedTitleMetadata",
    "MetadataStore",
    "TmdbCandidate",
    "TmdbClient",
    "TmdbDetails",
    "TmdbError",
]
