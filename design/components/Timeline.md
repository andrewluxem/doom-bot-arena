# Timeline

A match scrubber whose detail lane stacks one bot-coloured tick per frag, so burst fights read as towers, above an overview of the full 10:00 match.

Render with `DBA.timeline({buckets, compact, noHead, noOverview, stack})`.

- Detail window 00:00–02:30; the playhead (`02:14`, ink flag) splits played from future (dimmed, dashed baseline).
- Overview: elapsed fill, minute ticks, a bracket showing the detail window, `LIVE` marker. Drag the bracket to scrub; clicking a tick seeks to that frag.
- Snapshot: 62 frags in five bursts (~0:19, 0:46, 1:11, 1:36, 1:51–2:11).
