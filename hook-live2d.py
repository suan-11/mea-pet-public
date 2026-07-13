# hook-live2d.py
from PyInstaller.utils.hooks import collect_data_files

# 打包完整的 live2d
datas = collect_data_files('live2d')