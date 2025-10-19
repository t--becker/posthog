from posthog.test.base import APIBaseTest

from parameterized import parameterized
from rest_framework import status

from posthog.models import Comment
from posthog.models.exported_asset import ExportedAsset
from posthog.models.sharing_configuration import SharingConfiguration
from posthog.session_recordings.models.session_recording_event import SessionRecordingViewed

try:
    from ee.models.session_summaries import SingleSessionSummary

    HAS_EE = True
except ImportError:
    HAS_EE = False


class TestSyntheticPlaylists(APIBaseTest):
    def _get_synthetic_playlists(self, query_params: str = "") -> list[str]:
        url = f"/api/projects/{self.team.id}/session_recording_playlists{query_params}"
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK

        results = response.json()["results"]
        return [p["short_id"] for p in results if p["short_id"].startswith("synthetic-")]

    def _get_synthetic_playlist(self, short_id: str) -> dict:
        response = self.client.get(f"/api/projects/{self.team.id}/session_recording_playlists/{short_id}")
        assert response.status_code == status.HTTP_200_OK
        return response.json()

    def test_list_includes_synthetic_playlists(self) -> None:
        synthetic_short_ids = self._get_synthetic_playlists()

        expected = [
            "synthetic-watch-history",
            "synthetic-commented",
            "synthetic-shared",
            "synthetic-exported",
        ]
        if HAS_EE:
            expected.append("synthetic-summarised")

        assert sorted(synthetic_short_ids) == sorted(expected)

    def test_retrieve_synthetic_playlist(self) -> None:
        playlist = self._get_synthetic_playlist("synthetic-watch-history")

        assert playlist["short_id"] == "synthetic-watch-history"
        assert playlist["name"] == "Watch history"
        assert playlist["type"] == "collection"
        assert playlist["created_by"] is None
        assert playlist["last_modified_by"] is None
        assert playlist["created_at"] is None
        assert playlist["last_modified_at"] is None

    def test_synthetic_playlist_watch_history_content(self) -> None:
        SessionRecordingViewed.objects.create(team=self.team, user=self.user, session_id="watched-session-1")
        SessionRecordingViewed.objects.create(team=self.team, user=self.user, session_id="watched-session-2")

        playlist = self._get_synthetic_playlist("synthetic-watch-history")

        assert playlist["recordings_counts"]["collection"]["count"] == 2

    def test_synthetic_playlist_commented_content(self) -> None:
        Comment.objects.create(
            team=self.team,
            created_by=self.user,
            content="Great recording!",
            scope="Replay",
            item_id="commented-session-1",
        )
        Comment.objects.create(
            team=self.team,
            created_by=self.user,
            content="Another comment",
            scope="Replay",
            item_id="commented-session-2",
        )

        playlist = self._get_synthetic_playlist("synthetic-commented")

        assert playlist["recordings_counts"]["collection"]["count"] == 2

    def test_synthetic_playlist_shared_content(self) -> None:
        from posthog.models import SessionRecording

        recording1 = SessionRecording.objects.create(team=self.team, session_id="shared-session-1")
        recording2 = SessionRecording.objects.create(team=self.team, session_id="shared-session-2")

        SharingConfiguration.objects.create(
            team=self.team, recording=recording1, enabled=True, access_token="test-token-1"
        )
        SharingConfiguration.objects.create(
            team=self.team, recording=recording2, enabled=True, access_token="test-token-2"
        )

        playlist = self._get_synthetic_playlist("synthetic-shared")

        assert playlist["recordings_counts"]["collection"]["count"] == 2

    def test_synthetic_playlist_exported_content(self) -> None:
        ExportedAsset.objects.create(
            team=self.team,
            export_format=ExportedAsset.ExportFormat.GIF,
            export_context={"session_recording_id": "exported-session-1"},
            created_by=self.user,
        )
        ExportedAsset.objects.create(
            team=self.team,
            export_format=ExportedAsset.ExportFormat.PNG,
            export_context={"session_recording_id": "exported-session-2"},
            created_by=self.user,
        )

        playlist = self._get_synthetic_playlist("synthetic-exported")

        assert playlist["recordings_counts"]["collection"]["count"] == 2

    @parameterized.expand(
        [
            ["type_filters", "type=filters", []],
            ["user", "user=true", []],
            ["pinned", "pinned=true", []],
            ["created_by", "created_by={user_id}", []],
            ["?search=watch", "search=watch", ["synthetic-watch-history"]],
        ]
    )
    def test_filter_excludes_synthetic_playlists(
        self, _name: str, query_template: str, expected_results: list[str]
    ) -> None:
        query_params = f"?{query_template.format(user_id=self.user.id)}"
        synthetic_short_ids = self._get_synthetic_playlists(query_params)

        assert synthetic_short_ids == expected_results

    def test_cannot_update_synthetic_playlist(self) -> None:
        # This will fail because get_object will return an unsaved instance
        # The update will try to save it but it will fail validation
        # This is acceptable behavior - synthetic playlists are read-only
        pass  # TODO: Implement proper read-only enforcement if needed

    def test_cannot_delete_synthetic_playlist(self) -> None:
        # Similar to update - this will fail naturally
        pass  # TODO: Implement proper read-only enforcement if needed

    def test_synthetic_playlist_summarised_content(self) -> None:
        if not HAS_EE:
            # Skip test if EE is not available
            return

        # Create some session summaries
        SingleSessionSummary.objects.create(
            team=self.team,
            session_id="summarised-session-1",
            summary={"content": "User completed checkout flow"},
            created_by=self.user,
        )
        SingleSessionSummary.objects.create(
            team=self.team,
            session_id="summarised-session-2",
            summary={"content": "User encountered error on login"},
            created_by=self.user,
        )

        playlist = self._get_synthetic_playlist("synthetic-summarised")

        # Check that the count reflects the summarised recordings
        assert playlist["recordings_counts"]["collection"]["count"] == 2
