import struct
import unittest

import hdhr_frontend as hdhr


class ProtocolTests(unittest.TestCase):
    def test_device_id_is_valid(self):
        self.assertTrue(hdhr.valid_device_id(0xA1B2C3D2))
        self.assertFalse(hdhr.valid_device_id(0xA1B2C3D4))

    def test_discovery_reply_round_trip(self):
        packet_type, values = hdhr.parse_frame(hdhr.discovery_reply())
        self.assertEqual(packet_type, hdhr.DISCOVER_RPY)
        self.assertEqual(struct.unpack(">I", values[hdhr.TAG_DEVICE_ID][0])[0], hdhr.DEVICE_ID)
        self.assertEqual(values[hdhr.TAG_TUNER_COUNT][0], bytes((hdhr.TUNER_COUNT,)))
        self.assertEqual(values[hdhr.TAG_BASE_URL][0].rstrip(b"\0").decode(), hdhr.base_url())

    def test_request_matching(self):
        self.assertTrue(hdhr.matches_request({hdhr.TAG_DEVICE_TYPE: [struct.pack(">I", hdhr.WILDCARD)]}))
        self.assertTrue(hdhr.matches_request({hdhr.TAG_DEVICE_ID: [struct.pack(">I", hdhr.DEVICE_ID)]}))
        self.assertFalse(hdhr.matches_request({hdhr.TAG_DEVICE_ID: [struct.pack(">I", 0x12345678)]}))

    def test_parse_m3u(self):
        text = '''#EXTM3U
#EXTINF:-1 tvg-id="abc" tvg-name="News &amp; Talk" tvg-chno="12",Fallback
http://media-router.invalid/r/live/abc
'''
        self.assertEqual(hdhr.parse_m3u(text), [{
            "GuideNumber": "12",
            "GuideName": "News & Talk",
            "URL": "http://media-router.invalid/r/live/abc",
        }])

    def test_parse_m3u_rejects_non_http_urls(self):
        text = '#EXTM3U\n#EXTINF:-1,Unsafe\nfile:///etc/passwd\n'
        self.assertEqual(hdhr.parse_m3u(text), [])

    def test_parse_m3u_deduplicates_runtime_url(self):
        text = '''#EXTM3U
#EXTINF:-1 tvg-chno="7" group-title="First",First placement
http://media-router.invalid/r/live/channel_one
#EXTINF:-1 tvg-chno="700" group-title="Second",Duplicate placement
http://media-router.invalid/r/live/channel_one
'''
        self.assertEqual(hdhr.parse_m3u(text), [{
            "GuideNumber": "7",
            "GuideName": "First placement",
            "URL": "http://media-router.invalid/r/live/channel_one",
        }])


if __name__ == "__main__":
    unittest.main()
