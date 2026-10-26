import importlib.util
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "export_ripe_vip_link.py"
SPEC = importlib.util.spec_from_file_location("vip_exporter", SCRIPT)
vip = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vip)


def violation(root, *, rtt=0.949, distance=404.16, excess=309.30, hostname="nnn1-ham3"):
    return {"root": root, "hostname": hostname, "rtt_ms": rtt,
            "distance_km": distance, "excess_km": excess,
            "dns_lat": 53.63128, "dns_lon": 10.006414}


def probe(flag=True, details=None, *, lat=49.11, lon=8.42):
    if details is None:
        details = [violation("A"), violation("J", hostname="rootns-ham2")]
    return {"latitude": lat, "longitude": lon, "flags": {"100_2": flag}, "violations": details}


class VipExporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.data = self.base / "data"
        self.data.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def save_day(self, day, probes):
        path = self.data / f"{day}.json"
        path.write_text(json.dumps({"date": day, "slots": ["06", "12", "18"], "probes": probes}), encoding="utf-8")

    def test_two_independent_days_and_exact_schema(self):
        self.save_day("2026-10-01", {"15": probe(), "28": probe(), "35": probe(), "40": probe(False)})
        self.save_day("2026-10-02", {"15": probe(), "28": probe(False), "35": probe(False, [violation("A")]), "40": probe(False)})
        self.save_day("2026-10-03", {"12": probe(), "15": probe()})
        self.save_day("2026-10-04", {"12": probe(), "100": probe()})
        rows, available, missing = vip.build_feed(self.data, date(2026, 10, 1), date(2026, 10, 4))
        self.assertEqual([row["probe"] for row in rows], [12, 15])
        self.assertEqual(len(available), 4)
        self.assertEqual(missing, [])
        self.assertEqual(list(rows[0]), ["probe", "source", "source_email", "description", "date_detected", "lat", "lon"])
        self.assertEqual(rows[0]["source_email"], "ucsd_ripe_geolocation@googlegroups.com")
        self.assertEqual(rows[0]["date_detected"], int(datetime(2026, 10, 3, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(rows[0]["lat"], 49.11)
        self.assertEqual(rows[0]["lon"], 8.42)
        self.assertIn("server at site Hamburg", rows[0]["description"])
        self.assertIn("0.949ms", rows[0]["description"])
        self.assertIn("~404.16km", rows[0]["description"])
        self.assertNotIn("DNS", rows[0]["description"])
        self.assertNotIn("root", rows[0]["description"].lower())

        archive, latest, site = vip.write_feeds(rows, date(2026, 10, 15), self.base / "ripe_vip_link", self.base / "site" / "ripe_vip_link.jsonl")
        self.assertEqual(archive.name, "ripe_vip_link_20261015.jsonl")
        self.assertEqual(archive.read_bytes(), latest.read_bytes())
        self.assertEqual(archive.read_bytes(), site.read_bytes())
        decoded = [json.loads(line) for line in archive.read_text().splitlines()]
        self.assertEqual(decoded, rows)

    def test_two_letter_requirement_is_per_day_not_across_days(self):
        single_letter = [violation("A")]
        self.save_day("2026-10-01", {"9": probe(False, single_letter)})
        self.save_day("2026-10-02", {"9": probe(False, [violation("B", hostname="b1-fra")])})
        rows, _, _ = vip.build_feed(self.data, date(2026, 10, 1), date(2026, 10, 2))
        self.assertEqual(rows, [])

    def test_flag_inconsistency_fails_closed(self):
        self.save_day("2026-10-01", {"9": probe(details=[violation("A")])})
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            vip.build_feed(self.data, date(2026, 10, 1), date(2026, 10, 1))

    def test_dont_count_same_root_letter_twice(self):
        self.assertIsNone(vip.evidence_for_day(probe(details=[violation("A"), violation("A")])))

    def test_latest_qualifying_day_reports_disputed_coordinates(self):
        self.save_day("2026-10-01", {"15": probe(lat=49.11, lon=8.42)})
        newest_evidence = [violation("A", rtt=1.234, distance=555.22),
                           violation("J", rtt=1.234, distance=555.22)]
        self.save_day("2026-10-02", {"15": probe(lat=-33.4205, lon=-70.5985, details=newest_evidence)})
        rows, _, _ = vip.build_feed(self.data, date(2026, 10, 1), date(2026, 10, 2))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["lat"], rows[0]["lon"]), (-33.4205, -70.5985))
        self.assertIn("1.234ms", rows[0]["description"])
        self.assertIn("~555.22km", rows[0]["description"])
        self.assertEqual(rows[0]["date_detected"], int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()))

    def test_missing_reported_coordinates_fail_instead_of_null(self):
        self.save_day("2026-10-01", {"15": probe()})
        self.save_day("2026-10-02", {"15": probe(lat=None)})
        with self.assertRaisesRegex(ValueError, "reported coordinates"):
            vip.build_feed(self.data, date(2026, 10, 1), date(2026, 10, 2))

    def test_out_of_range_reported_coordinates_fail(self):
        self.save_day("2026-10-01", {"15": probe()})
        self.save_day("2026-10-02", {"15": probe(lat=91, lon=181)})
        with self.assertRaisesRegex(ValueError, "reported coordinates"):
            vip.build_feed(self.data, date(2026, 10, 1), date(2026, 10, 2))

    def test_zero_reported_coordinates_are_valid(self):
        self.save_day("2026-10-01", {"15": probe()})
        self.save_day("2026-10-02", {"15": probe(lat=0, lon=0)})
        rows, _, _ = vip.build_feed(self.data, date(2026, 10, 1), date(2026, 10, 2))
        self.assertEqual((rows[0]["lat"], rows[0]["lon"]), (0.0, 0.0))

    def test_release_boundaries(self):
        self.assertEqual(vip.release_window(date(2026, 10, 15)), (date(2026, 10, 1), date(2026, 10, 14)))
        self.assertEqual(vip.release_window(date(2026, 11, 1)), (date(2026, 10, 15), date(2026, 10, 31)))
        self.assertEqual(vip.latest_release_date(date(2026, 10, 26)), date(2026, 10, 15))
        self.assertRaises(ValueError, vip.release_window, date(2026, 10, 20))

    def test_source_site_fallback(self):
        sample = violation("F", hostname="arbitrary.example.org")
        self.assertIn("site near (53.631, 10.006)", vip.description_for(sample))


if __name__ == "__main__":
    unittest.main()
