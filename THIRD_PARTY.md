# 第三方代码与数据

- `pysc2-src/`：Google DeepMind PySC2，Apache-2.0。源版本为
  `05b28ef0d85aa5eef811bc49ff4c0cbe496c0adb`，许可证保存在
  `pysc2-src/LICENSE`。本项目保留了本机已有的 NumPy 颜色计算兼容修复
  和 Linux OSMesa 路径配置修改，安装时需使用此目录。
- `reference/python-sc2/`：python-sc2 技术关系参考数据，MIT。
  源版本记录见 `reference/python-sc2/source.json`，许可证保存在同目录。
- `arena/native_catalog.pb`、`arena/tech_recipe_map.json`：供技能和技术树
  使用的引擎元数据及关系表。实际对局仍以运行中的 SC2 引擎查询为准。

StarCraft II 游戏、地图安装、Proton、OSMesa 动态库及模型登录凭据由使用者
在本机准备，不包含在 Git 仓库中。
