# Mac mini playable fixture

This committed fixture describes one lab-only synthetic movie. The playlist is
kept separate from `catalog/vod-small.m3u`; repository commands do not import it.
Its locator is served only by the opt-in loopback fixture service.

`live-gateway.m3u` points at the same synthetic media as a lab-only live
channel so the reservation-aware gateway can be exercised without contacting
an external provider.

The MP4 is generated locally under
`.local/mac-mini/playback-fixture/media/playback-test.mp4` and remains ignored.
Its local SHA-256 and codec metadata are recorded beside it at generation time.
No copyrighted input media or credentials are used.
