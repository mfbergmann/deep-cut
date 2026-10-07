"""
Deep Cut: a last-resort finder for films Radarr cannot get.

Some films never appear on an indexer: early shorts, experimental work, TV
films, things that only survive as a DVD rip someone put on the Internet
Archive or an upload on YouTube. Deep Cut looks for those, scores what it
finds against what Radarr knows about the film, and puts candidates in front
of a person. Nothing is downloaded until someone approves it.
"""
