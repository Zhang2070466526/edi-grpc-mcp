# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — EDA MCP 本地服务打包
# 目录模式，对 NumPy/Matplotlib/gRPC 兼容性最好

import sys
from pathlib import Path

block_cipher = None
root = Path(SPECPATH).parent

a = Analysis(
    [str(root / 'start_servers.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / 'proto'), 'proto'),
        (str(root / 'servers' / 'chat' / 'index.html'), 'servers/chat'),
        (str(root / 'servers' / 'eda' / 'simulation_component_catalog.json'), 'servers/eda'),
    ],
    hiddenimports=[
        # MCP 核心
        'servers',
        'servers.registry_server',

        # Chat 模块
        'servers.chat',
        'servers.chat.service',
        'servers.chat.routes',

        # MCP Resources & Prompts
        'servers.resources_prompts',
        'servers.resources_prompts.resources',
        'servers.resources_prompts.prompts',

        # EDA 工具
        'servers.eda',
        'servers.eda.config',
        'servers.eda.grpc_client',
        'servers.eda.project_manage',
        'servers.eda.simulation',
        'servers.eda.simulation_components',
        'servers.eda.design_export',
        'servers.eda.model_replace',
        'servers.eda.edi_launcher',

        # TurboCharts 工具
        'servers.turbocharts',
        'servers.turbocharts.config',
        'servers.turbocharts.convert_raw',
        'servers.turbocharts.compare_results',

        # ANSYS 工具
        'servers.ansys',
        'servers.ansys.config',
        'servers.ansys.project_manage',
        'servers.ansys.run_analysis',

        # 图片 / 视觉 / 文档工具
        'servers.multimodal_vision',
        'servers.multimodal_vision.image_display',
        'servers.multimodal_vision.workspace_copy',
        'servers.multimodal_vision.vision_analyzer',
        'servers.multimodal_vision.document',
        'servers.multimodal_vision.validators',

        # 报告渲染
        'servers.report',
        'servers.report.generator',

        # 运行时配置
        'servers.utils',
        'servers.settings',
        'servers.process_guard',

        # Proto / gRPC
        'proto',
        'proto.ecserver_pb2',
        'proto.ecserver_pb2_grpc',
        'grpc',
        'grpc._cython',

        # 绘图（Agg 后端）
        'matplotlib',
        'matplotlib.backends.backend_agg',
        'numpy',
        'numpy.core._methods',

        # 依赖
        'dotenv',
        'starlette',
        'uvicorn',
        'uvicorn.loops',
        'uvicorn.protocols',

        # Windows COM（ANSYS 依赖）
        'pythoncom',
        'pywintypes',
        'win32com',
        'win32com.client',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'PyQt5', 'PyQt6',
        'PySide2', 'PySide6',
        'wx',
        'IPython', 'jupyter', 'notebook',
        'matplotlib.tests',
        'numpy.tests',
        # Pillow 可选格式插件（AVIF/BLP 未使用；保留 WebP 供 analyze_image 校验）
        'PIL.AvifImagePlugin', 'PIL.BlpImagePlugin',
        # matplotlib 3D 工具（未使用）
        'mpl_toolkits',
        # 文档模块（不能排除 unittest：matplotlib 依赖的 pyparsing.testing 会 import unittest）
        'pydoc', 'doctest',
    ],
    hooksconfig={
        'matplotlib': {
            'backends': ['Agg'],
        },
    },
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='edi_mcp_server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(root / 'scripts' / 'Logo.ico'),
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='edi-mcp',
)
