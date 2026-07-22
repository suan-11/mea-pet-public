# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['pet.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('live2d', 'live2d'),
        ('sprites', 'sprites'),
        ('models', 'models'),
        ('voice_cache', 'voice_cache'),
        ('GPT-Sovits', 'GPT-Sovits'),
        ('config.example.json', '.'),
        ('vits_models','vits_models'),
        ('vits_core', 'vits_core'),
        ('vits_requirements.txt', '.'),
        ('dic', 'dic'),
    ],
    hiddenimports=[
        # certifi CA bundle (belt-and-suspenders for httpx SSL verification)
        'certifi',
        # wizard package (dynamically imported from _reopen_setup_wizard)
        'wizard.app',
        'wizard.pages',
        'wizard.styles',
        'wizard.platform_info',
        'wizard.connection_test',
        'wizard.env_utils',
        'wizard.widgets',
        'wizard.page_env',
        'wizard.page_llm',
        'wizard.page_backend',
        'wizard.page_tts',
        'wizard.page_tts_vits',
        'wizard.page_tts_gsv',
        'wizard.page_tts_mimo',
        'wizard.page_vision',
        # desktop modules loaded dynamically
        'meapet.desktop.live2d_widget',
        'meapet.desktop.status_panel',
        'meapet.desktop.timeline_viewer',
        'meapet.desktop.chat_input',
        'meapet.desktop.dialogs',
        # agent adapters loaded via factory
        'meapet.agent.openclaw',
        # misc dynamic imports
        'translators',
        'jieba',
    ],
    hookspath=['.'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'test',
        'unittest',
        'pydoc',
        'doctest',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MeaPet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MeaPet',
)
