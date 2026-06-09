[app]
title = Navegacao UTAD
package.name = navegacaoutad
package.domain = pt.utad

source.dir = .
source.include_exts = py,osm,png,jpg,jpeg,jgw,pgw,jpgw,pngw,cal,joz,txt,md
source.include_patterns = OSM Pisos/*,Imagens ECT2/*
source.exclude_dirs = .git,.venv,venv,env,ENV,__pycache__,.tile_cache,build,dist,Nova pasta
source.exclude_exts = docx,pdf,zip

version = 0.1
requirements = python3,kivy
orientation = portrait
fullscreen = 0

android.permissions = INTERNET
android.allow_cleartext = 1
android.logcat_filters = *:S python:D

[buildozer]
log_level = 2
warn_on_root = 1
