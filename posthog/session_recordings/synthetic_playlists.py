"""
Synthetic playlists are virtual playlists that are dynamically calculated and available to all users.
They are not stored in the database but appear alongside regular playlists in the API.
"""

from collections.abc import Callable
from dataclasses import dataclass

from posthog.models import Comment, Team, User
from posthog.models.exported_asset import ExportedAsset
from posthog.models.sharing_configuration import SharingConfiguration
from posthog.session_recordings.models.session_recording_event import SessionRecordingViewed

try:
    from ee.models.session_summaries import SingleSessionSummary

    HAS_EE = True
except ImportError:
    HAS_EE = False


@dataclass
class SyntheticPlaylistDefinition:
    """Definition of a synthetic playlist that will be computed on-demand"""

    id: int
    short_id: str
    name: str
    description: str
    type: str  # Should be "collection"
    get_session_ids: Callable[[Team, User], list[str]]
    count_session_ids: Callable[[Team, User], int]
    # Synthetic playlists don't have filters in the traditional sense,
    # but we can store metadata about how they're generated
    metadata: dict


def _base_watched_queryset(team: Team, user: User):
    """Base queryset for watched session recordings"""
    return SessionRecordingViewed.objects.filter(team=team, user=user)


def get_watched_session_ids(team: Team, user: User) -> list[str]:
    """Get all session IDs the user has watched"""
    return list(_base_watched_queryset(team, user).order_by("-created_at").values_list("session_id", flat=True))


def count_watched_session_ids(team: Team, user: User) -> int:
    """Count session IDs the user has watched"""
    return _base_watched_queryset(team, user).count()


def _base_commented_queryset(team: Team, user: User):
    """Base queryset for commented session recordings"""
    return Comment.objects.filter(team=team, scope="Replay", deleted=False).exclude(item_id__isnull=True)


def get_commented_session_ids(team: Team, user: User) -> list[str]:
    """Get all session IDs that have comments from anyone on the team"""
    return list(_base_commented_queryset(team, user).values_list("item_id", flat=True).distinct())


def count_commented_session_ids(team: Team, user: User) -> int:
    """Count session IDs that have comments from anyone on the team"""
    return _base_commented_queryset(team, user).values("item_id").distinct().count()


def _base_shared_queryset(team: Team, user: User):
    """Base queryset for shared session recordings"""
    return SharingConfiguration.objects.filter(team=team, enabled=True).exclude(recording__isnull=True)


def get_shared_session_ids(team: Team, user: User) -> list[str]:
    """Get all session IDs that have been shared"""
    return list(_base_shared_queryset(team, user).values_list("recording__session_id", flat=True).distinct())


def count_shared_session_ids(team: Team, user: User) -> int:
    """Count session IDs that have been shared"""
    return _base_shared_queryset(team, user).values("recording__session_id").distinct().count()


def _base_summarised_queryset(team: Team, user: User):
    """Base queryset for summarised session recordings"""
    if not HAS_EE:
        return None
    return SingleSessionSummary.objects.filter(team=team)


def get_summarised_session_ids(team: Team, user: User) -> list[str]:
    """Get all session IDs that have AI-generated summaries"""
    qs = _base_summarised_queryset(team, user)
    if qs is None:
        return []
    return list(qs.order_by("-created_at").values_list("session_id", flat=True).distinct())


def count_summarised_session_ids(team: Team, user: User) -> int:
    """Count session IDs that have AI-generated summaries"""
    qs = _base_summarised_queryset(team, user)
    if qs is None:
        return 0
    return qs.values("session_id").distinct().count()


def _base_exported_queryset(team: Team, user: User):
    """Base queryset for exported session recordings"""
    return (
        ExportedAsset.objects.filter(team=team)
        .filter(export_context__has_key="session_recording_id")
        .exclude(export_context__session_recording_id__isnull=True)
        .exclude(export_context__session_recording_id="")
    )


def get_exported_session_ids(team: Team, user: User) -> list[str]:
    """Get all session IDs that have been exported (clipped to GIF or screenshot)"""
    session_ids = (
        _base_exported_queryset(team, user)
        .order_by("-created_at")
        .values_list("export_context__session_recording_id", flat=True)
    )
    # Remove duplicates while preserving order (most recent first)
    return list(dict.fromkeys(session_ids))


def count_exported_session_ids(team: Team, user: User) -> int:
    """Count session IDs that have been exported"""
    return _base_exported_queryset(team, user).values("export_context__session_recording_id").distinct().count()


# Registry of all synthetic playlists
def _get_synthetic_playlists() -> list[SyntheticPlaylistDefinition]:
    """Build the list of synthetic playlists, conditionally including EE features"""
    playlists = [
        SyntheticPlaylistDefinition(
            id=-1,
            short_id="synthetic-watch-history",
            name="Watch history",
            description="Recordings you have watched",
            type="collection",
            get_session_ids=get_watched_session_ids,
            count_session_ids=count_watched_session_ids,
            metadata={"icon": "IconEye", "is_user_specific": True},
        ),
        SyntheticPlaylistDefinition(
            id=-2,
            short_id="synthetic-commented",
            name="Recordings with comments",
            description="Recordings that have team comments",
            type="collection",
            get_session_ids=get_commented_session_ids,
            count_session_ids=count_commented_session_ids,
            metadata={"icon": "IconComment", "is_user_specific": False},
        ),
        SyntheticPlaylistDefinition(
            id=-3,
            short_id="synthetic-shared",
            name="Shared recordings",
            description="Recordings that have been shared externally",
            type="collection",
            get_session_ids=get_shared_session_ids,
            count_session_ids=count_shared_session_ids,
            metadata={"icon": "IconShare", "is_user_specific": False},
        ),
        SyntheticPlaylistDefinition(
            id=-4,
            short_id="synthetic-exported",
            name="Exported recordings",
            description="Recordings that have been exported as clips or screenshots",
            type="collection",
            get_session_ids=get_exported_session_ids,
            count_session_ids=count_exported_session_ids,
            metadata={"icon": "IconDownload", "is_user_specific": False},
        ),
    ]

    # Only add summarised playlist if EE is available
    if HAS_EE:
        playlists.append(
            SyntheticPlaylistDefinition(
                id=-5,
                short_id="synthetic-summarised",
                name="Summarised sessions",
                description="Sessions with AI-generated summaries. Ask PostHog AI to summarize sessions for you.",
                type="collection",
                get_session_ids=get_summarised_session_ids,
                count_session_ids=count_summarised_session_ids,
                metadata={"icon": "IconSparkles", "is_user_specific": False},
            )
        )

    return playlists


SYNTHETIC_PLAYLISTS: list[SyntheticPlaylistDefinition] = _get_synthetic_playlists()


def get_synthetic_playlist(short_id: str) -> SyntheticPlaylistDefinition | None:
    """Get a synthetic playlist definition by short_id"""
    for playlist in SYNTHETIC_PLAYLISTS:
        if playlist.short_id == short_id:
            return playlist
    return None
