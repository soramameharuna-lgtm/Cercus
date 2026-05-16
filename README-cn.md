# Cercus 实验控制框架使用指南

**Cercus** 是一个面向高时间精度生物行为学与神经科学实验的多进程、闭环刺激控制框架。系统实现了 UI 调度、视觉渲染、硬件遥测与数据持久化的物理级解耦。

## 1. 架构概览

系统强制执行单向数据流与功能隔离：

* **主控中枢 (`dashboard.py`)**：非阻塞 GUI，负责参数装配、动态表单生成与低频状态监控。
* **纯逻辑计算核 (`paradigm.py`)**：数学模型层。基于时间差与硬件反馈输出标准化渲染指令流与遥测状态。
* **无状态渲染引擎 (`core_render.py`)**：执行基础几何绘制指令（支持 `circle`, `rect`, `element_array` 等渲染类型）。
* **异步硬件驱动 (`core_hardware.py`)**：处理高频传感器数据采集与触发信号下发。
* **双轨数据记录 (`core_logger.py`)**：分离高频运动学遥测数据与低频实验事件流。

## 2. 核心功能与执行模式

* **双轨执行模式**：
* **Auto (自动模式)**：基于设定的 ITI/ISI 区间全自动连续执行实验流程。
* **Manual (手动模式)**：在 ITI 结束后渲染进程安全挂起，等待外部按键（`Space`）精准触发单次试次。


* **数字孪生监控**：控制端包含微缩监视器模块，等比映射物理双屏刺激状态。
* **硬件虚拟流**：无硬件接入时，支持配置串口为 `mock` 注入虚拟闭环数据以保障调试链路贯通。

## 3. 内置实验范式 (Paradigms)

当前内置四套标准范式矩阵，均可通过主控 UI 面板动态装载与配置：

1. **多模态逼近 (Looming Paradigm)**：
* 包含纯视觉与纯风场基线。
* 包含 7 组从 TTC -373ms 到 +200ms 梯度风场标定的多感觉融合刺激。


2. **经典视觉逼近 (ClassicLooming Paradigm)**：
* 纯视觉参数化模型，支持动态配置碰撞比、起始/终止视角及左右呈现逻辑。


3. **光流场 (OpticFlow Paradigm)**：
* 矢量化粒子运动模型，支持配置速度、密度、相干性与运动方向。


4. **运动轨迹 (MovementTrace Paradigm)**：
* 利萨茹曲线轨迹跟踪，支持配置X/Y轴频率、振幅与拖影长度。



## 4. 环境安装与运行

建议在隔离的虚拟环境（如 Conda）中运行本系统，安装核心依赖项：

```bash
pip install -r requirements.txt
```

执行入口文件启动控制台：

```bash
python main.py
```

## 5. 数据产出规范

双轨记录文件于 `data/` 目录自动生成并基于 `global_trial_id` 对齐时间戳：

1. `{Subject}_session_{n}_events.csv`：记录低频实验状态流事件与参数详情。
2. `{Subject}_session_{n}_kinematics.csv`：记录闭环硬件遥测数据矩阵。

## 6. 框架扩展：添加新范式 (Paradigms)

扩展新实验范式无需修改底层渲染容器或控制流代码，仅需在 `src/models/paradigm.py` 中执行以下开发规范：

1. **继承基类**：新建核心计算类并强制继承 `BaseParadigm` 抽象类。
2. **定义前端映射接口**：
* `get_available_patterns(cls)`：返回该范式支持的具体模式名称列表。
* `get_parameter_schema(cls)`：声明动态UI参数配置字典。框架依据字典中的 `type` 字段（如 `int`, `float`, `choice`, `bool`）自动渲染主控表单并将最终取值注入实例化上下文。


3. **实现核心生命周期**：
* `generate_trials(self, pattern_key)`：根据用户选择的模式，构建并返回当前 Session 的全体试次上下文配置（`List[dict]`）。
* `prepare_trial(self, trial_context)`：返回当前试次开始前需下发至硬件的初始化串口指令（如无则返回空字符串）。
* `get_idle_frame(self, hw_telemetry)`：输出 ITI/ISI 等空闲阶段的稳态渲染指令，需返回元组 `(cmds, telemetry_dict, sync_states)`。
* `process_frame(self, elapsed_time, trial_context, hw_telemetry)`：帧级闭环计算核心。根据当前时间戳与硬件遥测输入完成坐标更新，返回帧状态元组 `(is_done, cmds, telemetry_dict, sync_states)`。


4. **返回标准指令**：上述生命周期中返回的 `cmds` 需使用底层容器支持的标准化字典（例如 `type="rect"`, `type="element_array"`，配合 `pos`, `colors`, `sizes` 等参数）。
5. **全局注册**：新定义的范式类必须加入 `src/models/paradigm.py` 脚本底部的 `PARADIGM_REGISTRY` 字典进行注册，方可被主控面板解析调用。