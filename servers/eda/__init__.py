r"""EDA gRPC MCP 工具包 -- 通过 ExternalCall gRPC 操作 EDI 工程（46 个工具）。

按文件列工具与用途：

config.py — 配置与路径检测（无工具，供其他模块复用）
    EDA_GRPC_SERVER / EDI_PATH / TURBOCHARTS_PATH / SIM_COMPONENT_TYPES / validate_project_path

project_reader.py — 工程文件解析（无工具）
    ProjectReader / parse_sexp / parse_components / parse_paramsinfo

grpc_client.py — gRPC 通信层（内部）
    call_grpc / call_project_grpc

project_manage.py — 工程管理（9 工具）
    list_epp_projects             扫描文件夹中的所有 .epp 工程
    create_project                创建新工程（不显示向导、不自动打开）
    open_edi_project              打开 .epp 工程
    close_edi_project             关闭已打开的工程
    get_project_summary           工程概览（元数据/原理图/仿真配置）
    analyze_variables             分析变量定义和引用关系
    list_schematic_components     查询原理图全部器件（gRPC，含完整参数）
    get_schematic_component_info  按实例名查询器件完整信息
    get_components_static_params  查询器件固有参数（重量/尺寸/封装/厂商/成本）

simulation.py — 仿真（8 工具）
    simulate_project              同步执行工程仿真
    start_simulation_async        启动异步仿真，返回 task_id
    get_simulation_async_status   查询异步仿真进度和日志
    get_simulation_async_result   获取异步仿真最终结果
    list_eda_tasks                列出异步仿真任务
    simulate_netlist              仿真网表文件
    simulate_netlist_with_ads     ADS 仿真控制器
    simulate_anti_burnout         抗烧毁仿真与风险评估

simulation_components.py — 仿真器件（10 工具）
    get_simulation_component_schema  查询器件参数 schema 和权限
    list_simulation_components       列出工程中的仿真器件（支持 summary_only）
    create_simulation_component      新增器件（EDI 默认参数）
    update_simulation_component      按实例名更新参数（三路类型推断）
    replace_port_component           替换端口器件类型（TermG↔P_nToneG）
    delete_simulation_component      按实例名删除器件
    set_component_active_state       确定性设置 NORMAL/DISABLED/SHORTED
    generate_schematic_from_netlist  从网表生成原理图（清空需双重确认）
    replace_schematic_from_file      从 .ep 文件整体替换原理图
    attach_out_component             为器件引脚挂载 Out 器件

design_export.py — 网表/截图（2 工具）
    export_project_netlist  查看/导出工程网表文件
    capture_schematic       截取原理图并保存为图片

signal_chain.py — 信号链追踪（1 工具）
    get_signal_chain  解析网表按「节点↔器件接力」追踪信号流

model_replace.py — 模型替换（1 工具）
    replace_models_from_csv  根据 CSV 文件批量替换元件模型

model_library.py — 模型库（5 工具）
    get_model_category_params           获取模型分类及参数列表
    search_public_models                按子类查询公共模型库
    search_personal_models              按子类查询个人模型库
    load_performance_component_from_mms 从 MMS 导入性能模型到本地模型库
    add_performance_component           将模型库 Component 放置到原理图

edi_launcher.py — 启动/诊断（3 工具）
    launch_edi          启动 EDI 客户端并等待 gRPC 就绪
    get_service_status  返回 gRPC 通道状态、队列占用
    get_service_logs    读取 EDI 服务端日志并分析异常

workspace_ops.py — 工作区（3 工具）
    create_workspace                       创建工作区（不自动切换）
    switch_workspace                       设置下次启动使用的工作区
    get_current_workspace                  查询当前实际加载的工作区目录

schematic_ops.py — 原理图扩展操作（4 工具）
    list_ideal_components     列出内置器件类型及说明
    add_ideal_component       按指定坐标新增内置器件
    clear_schematic           清空原理图（破坏性，需 confirm_clear）
    add_wire                  连接两个器件的指定引脚
"""

# -- 工程管理 --
from servers.eda.project_manage import (  # noqa: F401
    list_epp_projects,
    create_project,
    open_edi_project,
    close_edi_project,
    get_project_summary,
    analyze_variables,
    list_schematic_components,
    get_schematic_component_info,
    get_components_static_params,
)

# -- 仿真 --
from servers.eda.simulation import (  # noqa: F401
    simulate_project,
    simulate_netlist,
    simulate_netlist_with_ads,
    simulate_anti_burnout,
    start_simulation_async,
    get_simulation_async_status,
    get_simulation_async_result,
    list_eda_tasks,
)

# -- 仿真器件 --
from servers.eda.simulation_components import (  # noqa: F401
    get_simulation_component_schema,
    list_simulation_components,
    create_simulation_component,
    update_simulation_component,
    replace_port_component,
    delete_simulation_component,
    set_component_active_state,
    generate_schematic_from_netlist,
    replace_schematic_from_file,
    attach_out_component,
)

# -- 网表/截图 --
from servers.eda.design_export import (  # noqa: F401
    export_project_netlist,
    capture_schematic,
)

# -- 信号链追踪 --
from servers.eda.signal_chain import get_signal_chain  # noqa: F401

# -- 模型替换 --
from servers.eda.model_replace import replace_models_from_csv  # noqa: F401

# -- 模型库 --
from servers.eda.model_library import (  # noqa: F401
    get_model_category_params,
    search_public_models,
    search_personal_models,
    load_performance_component_from_mms,
    add_performance_component,
)

# -- 启动/诊断 --
from servers.eda.edi_launcher import (  # noqa: F401
    launch_edi,
    get_service_status,
    get_service_logs,
)

# -- 工作区 --
from servers.eda.workspace_ops import (  # noqa: F401
    create_workspace,
    switch_workspace,
    get_current_workspace,
)

# -- 原理图扩展操作 --
from servers.eda.schematic_ops import (  # noqa: F401
    list_ideal_components,
    add_ideal_component,
    clear_schematic,
    add_wire,
)
