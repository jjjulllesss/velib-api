import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from api.index import app

class TestVelibCommute(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        
    def test_missing_params(self) -> None:
        response = self.client.get("/api/commute")
        self.assertEqual(response.status_code, 400)
        self.assertIn("obligatoire", response.json()["detail"])
        
    def test_invalid_params(self) -> None:
        response = self.client.get("/api/commute?start=123,abc&end=456")
        self.assertEqual(response.status_code, 400)
        self.assertIn("invalide", response.json()["detail"])
        
    def test_empty_params(self) -> None:
        response = self.client.get("/api/commute?start=&end=456")
        self.assertEqual(response.status_code, 400)
        
    @patch("api.index.fetch_all_stations", new_callable=AsyncMock)
    def test_all_upstream_failed(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = {}
        response = self.client.get("/api/commute?start=1001&end=2001")
        self.assertEqual(response.status_code, 502)
        
    @patch("api.index.fetch_all_stations", new_callable=AsyncMock)
    def test_successful_traversal_no_fallback(self, mock_fetch: AsyncMock) -> None:
        # Setup mock data where primary start has bikes, and primary end has docks
        mock_fetch.return_value = {
            1001: {
                "name": "Station Depart 1",
                "docks_available": 10,
                "bikes": [
                    {"id": "bike_low_score", "type": "mechanical", "status": "available", "score": 90, "bikeRate": 3, "lastRideTime": "2026-06-20T14:00:00Z", "dockPosition": "1"},
                    {"id": "bike_high_score_old", "type": "mechanical", "status": "available", "score": 100, "bikeRate": 3, "lastRideTime": "2026-06-20T12:00:00Z", "dockPosition": "2"},
                    {"id": "bike_high_score_new", "type": "mechanical", "status": "available", "score": 100, "bikeRate": 3, "lastRideTime": "2026-06-20T14:30:00Z", "dockPosition": "3"},
                    {"id": "bike_electric", "type": "electric", "status": "available", "score": 100, "bikeRate": 3, "lastRideTime": "2026-06-20T15:00:00Z", "battery_level": 50, "dockPosition": "4"},
                ]
            },
            2001: {
                "name": "Station Arrivee 1",
                "docks_available": 5,
                "bikes": []
            }
        }
        
        response = self.client.get("/api/commute?start=1001,1002&end=2001,2002")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["start_fallback_used"])
        self.assertFalse(data["no_mechanical_available"])
        self.assertFalse(data["end_fallback_used"])
        self.assertFalse(data["no_docks_available"])
        
        # Verify selected mechanical bikes sorting and capacity limit (max 3, and only mechanical)
        mech_bikes = data["selected_mechanical_bikes"]
        self.assertEqual(len(mech_bikes), 3)  # we have 3 mechanical
        self.assertEqual(mech_bikes[0]["id"], "bike_high_score_new")  # highest score & most recent
        self.assertEqual(mech_bikes[1]["id"], "bike_high_score_old")  # highest score but older
        self.assertEqual(mech_bikes[2]["id"], "bike_low_score")  # lower score

        # Verify selected electric bikes (should have the electric one)
        elec_bikes = data["selected_electric_bikes"]
        self.assertEqual(len(elec_bikes), 1)
        self.assertEqual(elec_bikes[0]["id"], "bike_electric")
        
        # Verify end station details
        self.assertEqual(data["end_station_used"]["id"], 2001)
        self.assertEqual(data["end_station_used"]["docks_available"], 5)
        
        # Verify summary
        expected_summary = (
            "Mechanical:\n"
            "- 3 (100) Station Depart 1\n"
            "- 2 (100) Station Depart 1\n"
            "- 1 (90) Station Depart 1\n\n"
            "Electrical:\n"
            "- 4 (100) Station Depart 1\n\n"
            "Arrival: 5 - Station Arrivee 1"
        )
        self.assertEqual(data["summary"], expected_summary)

    @patch("api.index.fetch_all_stations", new_callable=AsyncMock)
    def test_start_fallback_and_end_fallback(self, mock_fetch: AsyncMock) -> None:
        # Setup mock data where primary start has no bikes (only electric/unavailable),
        # but alternative start has bikes.
        # Primary end has 0 docks, alternative end has docks.
        mock_fetch.return_value = {
            1001: {
                "name": "Station Depart 1",
                "docks_available": 10,
                "bikes": [
                    {"id": "bike_unavailable", "type": "mechanical", "status": "rented", "score": 100, "bikeRate": 3, "dockPosition": "1"}
                ]
            },
            1002: {
                "name": "Station Depart 2 (Alternative)",
                "docks_available": 10,
                "bikes": [
                    {"id": "bike_mech_alt", "type": "mechanical", "status": "available", "score": 95, "bikeRate": 3, "lastRideTime": "2026-06-20T14:00:00Z", "dockPosition": "5"}
                ]
            },
            2001: {
                "name": "Station Arrivee 1",
                "docks_available": 0,
                "bikes": []
            },
            2002: {
                "name": "Station Arrivee 2 (Alternative)",
                "docks_available": 12,
                "bikes": []
            }
        }
        
        response = self.client.get("/api/commute?start=1001,1002&end=2001,2002")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Start checks
        self.assertTrue(data["start_fallback_used"])
        self.assertEqual(data["start_station_used"]["id"], 1002)
        self.assertEqual(len(data["selected_mechanical_bikes"]), 1)
        self.assertEqual(data["selected_mechanical_bikes"][0]["id"], "bike_mech_alt")
        
        # End checks
        self.assertTrue(data["end_fallback_used"])
        self.assertEqual(data["end_station_used"]["id"], 2002)
        self.assertEqual(data["end_station_used"]["docks_available"], 12)
        
        # Summary checks
        expected_summary = (
            "Mechanical:\n"
            "- 5 (95) Station Depart 2 (Alternative)\n\n"
            "Electrical:\n"
            "- None\n\n"
            "Arrival: 12 - Station Arrivee 2 (Alternative)"
        )
        self.assertEqual(data["summary"], expected_summary)

    @patch("api.index.fetch_all_stations", new_callable=AsyncMock)
    def test_no_bikes_no_docks_anywhere(self, mock_fetch: AsyncMock) -> None:
        # Setup mock data where no stations have bikes or docks
        mock_fetch.return_value = {
            1001: {
                "name": "Station Depart 1",
                "docks_available": 0,
                "bikes": []
            },
            2001: {
                "name": "Station Arrivee 1",
                "docks_available": 0,
                "bikes": []
            }
        }
        
        response = self.client.get("/api/commute?start=1001&end=2001")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertTrue(data["no_mechanical_available"])
        self.assertTrue(data["no_electric_available"])
        self.assertIsNone(data["start_station_used"])
        self.assertEqual(data["selected_mechanical_bikes"], [])
        self.assertEqual(data["selected_electric_bikes"], [])
        
        self.assertTrue(data["no_docks_available"])
        self.assertEqual(data["end_station_used"]["id"], 2001)
        
        # Summary checks
        expected_summary = (
            "Mechanical:\n"
            "- None\n\n"
            "Electrical:\n"
            "- None\n\n"
            "Arrival: 0 - Station Arrivee 1"
        )
        self.assertEqual(data["summary"], expected_summary)

    @patch("api.index.fetch_all_stations", new_callable=AsyncMock)
    def test_deduplication_and_capping(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = {
            1001: {
                "name": "Station 1001",
                "docks_available": 10,
                "bikes": [
                    {"id": "b1", "type": "mechanical", "status": "available", "score": 90, "bikeRate": 3},
                    {"id": "b2", "type": "mechanical", "status": "available", "score": 90, "bikeRate": 3}
                ]
            },
            2001: {
                "name": "Station 2001",
                "docks_available": 5,
                "bikes": []
            }
        }
        
        # Call with duplicates and more than 5 stations
        response = self.client.get(
            "/api/commute?start=1001,1001,1001,1002,1003,1004,1005,1006&end=2001,2001,2002,2003,2004,2005,2006"
        )
        self.assertEqual(response.status_code, 200)
        
        # Verify fetch_all_stations was called with deduplicated and capped lists
        # Expected start: [1001, 1002, 1003, 1004, 1005] (first 5 unique)
        # Expected end: [2001, 2002, 2003, 2004, 2005] (first 5 unique)
        called_ids = mock_fetch.call_args[0][0]
        self.assertEqual(len(called_ids), 10)
        self.assertEqual(called_ids[:5], [1001, 1002, 1003, 1004, 1005])
        self.assertEqual(called_ids[5:], [2001, 2002, 2003, 2004, 2005])

    @patch("api.index.fetch_all_stations", new_callable=AsyncMock)
    def test_single_bike_at_primary_returns_it_plus_alternatives(self, mock_fetch: AsyncMock) -> None:
        # If only one velib is available at the station, check the other stations to give more options
        mock_fetch.return_value = {
            1001: {
                "name": "Station Depart 1",
                "docks_available": 10,
                "bikes": [
                    {"id": "bike_primary_1", "type": "mechanical", "status": "available", "score": 90, "bikeRate": 3, "dockPosition": "1"}
                ]
            },
            1002: {
                "name": "Station Depart 2",
                "docks_available": 10,
                "bikes": [
                    {"id": "bike_secondary_1", "type": "mechanical", "status": "available", "score": 95, "bikeRate": 3, "dockPosition": "2"},
                    {"id": "bike_secondary_2", "type": "mechanical", "status": "available", "score": 90, "bikeRate": 3, "dockPosition": "3"}
                ]
            },
            2001: {
                "name": "Station Arrivee 1",
                "docks_available": 5,
                "bikes": []
            }
        }

        response = self.client.get("/api/commute?start=1001,1002&end=2001")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["start_fallback_used"])  # Because we also used an alternative station
        self.assertEqual(len(data["selected_mechanical_bikes"]), 3)
        self.assertEqual(data["selected_mechanical_bikes"][0]["id"], "bike_primary_1")
        self.assertEqual(data["selected_mechanical_bikes"][1]["id"], "bike_secondary_1")
        self.assertEqual(data["selected_mechanical_bikes"][2]["id"], "bike_secondary_2")

        expected_summary = (
            "Mechanical:\n"
            "- 1 (90) Station Depart 1\n"
            "- 2 (95) Station Depart 2\n"
            "- 3 (90) Station Depart 2\n\n"
            "Electrical:\n"
            "- None\n\n"
            "Arrival: 5 - Station Arrivee 1"
        )
        self.assertEqual(data["summary"], expected_summary)

    @patch("api.index.fetch_all_stations", new_callable=AsyncMock)
    def test_no_bike_above_score_80_at_primary_checks_other_stations(self, mock_fetch: AsyncMock) -> None:
        # If no velib above the score of 80, search for the other stations.
        mock_fetch.return_value = {
            1001: {
                "name": "Station Depart 1",
                "docks_available": 10,
                "bikes": [
                    {"id": "bike_primary_1", "type": "mechanical", "status": "available", "score": 70, "bikeRate": 3, "dockPosition": "1"},
                    {"id": "bike_primary_2", "type": "mechanical", "status": "available", "score": 60, "bikeRate": 3, "dockPosition": "2"}
                ]
            },
            1002: {
                "name": "Station Depart 2",
                "docks_available": 10,
                "bikes": [
                    {"id": "bike_secondary_1", "type": "mechanical", "status": "available", "score": 85, "bikeRate": 3, "dockPosition": "3"}
                ]
            },
            2001: {
                "name": "Station Arrivee 1",
                "docks_available": 5,
                "bikes": []
            }
        }

        response = self.client.get("/api/commute?start=1001,1002&end=2001")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["start_fallback_used"])
        self.assertEqual(len(data["selected_mechanical_bikes"]), 3)
        self.assertEqual(data["selected_mechanical_bikes"][0]["id"], "bike_primary_1")
        self.assertEqual(data["selected_mechanical_bikes"][1]["id"], "bike_primary_2")
        self.assertEqual(data["selected_mechanical_bikes"][2]["id"], "bike_secondary_1")

        expected_summary = (
            "Mechanical:\n"
            "- 1 (70) Station Depart 1\n"
            "- 2 (60) Station Depart 1\n"
            "- 3 (85) Station Depart 2\n\n"
            "Electrical:\n"
            "- None\n\n"
            "Arrival: 5 - Station Arrivee 1"
        )
        self.assertEqual(data["summary"], expected_summary)

    @patch("api.index.fetch_all_stations", new_callable=AsyncMock)
    def test_new_filtering_rules_bikerate_and_battery(self, mock_fetch: AsyncMock) -> None:
        # Bikes with a bikeRate different of 3 should be not considered.
        # For the electrical, a battery level below 20%, the bike is not considered.
        mock_fetch.return_value = {
            1001: {
                "name": "Station Depart 1",
                "docks_available": 10,
                "bikes": [
                    {"id": "bike_bad_rate", "type": "mechanical", "status": "available", "score": 100, "bikeRate": 2},
                    {"id": "bike_good_rate", "type": "mechanical", "status": "available", "score": 90, "bikeRate": 3},
                    {"id": "elec_bad_rate", "type": "electric", "status": "available", "score": 100, "bikeRate": 2, "battery_level": 80},
                    {"id": "elec_low_battery", "type": "electric", "status": "available", "score": 100, "bikeRate": 3, "battery_level": 15},
                    {"id": "elec_good", "type": "electric", "status": "available", "score": 90, "bikeRate": 3, "battery_level": 25}
                ]
            },
            2001: {
                "name": "Station Arrivee 1",
                "docks_available": 5,
                "bikes": []
            }
        }

        response = self.client.get("/api/commute?start=1001&end=2001")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])

        # Should only have bike_good_rate for mechanical
        self.assertEqual(len(data["selected_mechanical_bikes"]), 1)
        self.assertEqual(data["selected_mechanical_bikes"][0]["id"], "bike_good_rate")

        # Should only have elec_good for electric
        self.assertEqual(len(data["selected_electric_bikes"]), 1)
        self.assertEqual(data["selected_electric_bikes"][0]["id"], "elec_good")

if __name__ == "__main__":
    unittest.main()
