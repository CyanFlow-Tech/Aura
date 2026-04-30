import copy
import inspect
import os
import re
from dataclasses import is_dataclass
from pathlib import Path
from typing import (
    Annotated, Any, Dict, List, NamedTuple, Optional, Tuple,
    Type, TypeVar, Union, get_args, get_origin, get_type_hints
)

import yaml
from typeguard import check_type
from .polymorphic import FactoryMixin

ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


class ParamInfo(NamedTuple):
    name: str
    type: Optional[Any]
    default: Optional[Any]
    doc: Optional[str]


class AutoConfigMixin:
    _params: List[ParamInfo]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._params = analyze_cls_params(cls)

def analyze_annotation(annotation: Any) -> Tuple[Any, Any]:
    if get_origin(annotation) is not Annotated:
        return annotation, None
    return get_args(annotation)

def _cast_empty_to_none(value: Any) -> Optional[Any]:
    if value is inspect._empty:
        return None
    return value

def analyze_cls_params(klass: Type[AutoConfigMixin]) -> List[ParamInfo]:
    params = []
    init_signature = inspect.signature(klass.__init__)
    type_hints = get_type_hints(klass.__init__, include_extras=True)
    for parameter in init_signature.parameters.values():
        param_name = parameter.name
        if param_name == "self":
            continue
        annotation = type_hints.get(param_name, parameter.annotation)
        param_type, param_doc = analyze_annotation(annotation)
        param_type = _cast_empty_to_none(param_type)
        param_default = copy.deepcopy(parameter.default)
        param_default = _cast_empty_to_none(param_default)
        params.append(ParamInfo(
            param_name,
            param_type,
            param_default,
            param_doc,
        ))
    return params

def _parse_env_variable(value: Any) -> Any:
    if isinstance(value, str):
        match = ENV_PATTERN.fullmatch(value)
        if match:
            return os.getenv(match.group(1), "")
        return value
    if isinstance(value, list):
        return [_parse_env_variable(item) for item in value]
    if isinstance(value, dict):
        return {key: _parse_env_variable(item) for key, item in value.items()}
    return value

def parse_env_variable(value: Dict[str, Any]) -> Dict[str, Any]:
    return _parse_env_variable(value)

def override_config(target: Any, overrides: dict[str, Any]) -> None:
    for field_name, override_value in overrides.items():
        try:
            current_value = getattr(target, field_name)
        except AttributeError as e:
            raise AttributeError(
                f"Attribute `{field_name}` from YAML is not a correct field or tool class") from e
        if not is_dataclass(current_value):
            setattr(target, field_name, override_value)
        else:
            override_config(current_value, override_value)


def build_tool_config(tool: Type[FactoryMixin], overrides: dict[str, Any]) -> dict[str, Any]:
    config = {}
    extra_keys = set(overrides.keys()) - (set(tool._implements.keys())|{'implement'})
    if extra_keys:
        raise ValueError(f"Invalid attributes for `{tool.__name__}`: {', '.join(extra_keys)}")
    implement = overrides.get('implement', tool.implement)
    if implement is None:
        raise ValueError(f"Attribute `implement` is not set for {tool.__name__}")
    overrides = overrides.get(implement, {})
    for param in tool.get_impl(implement)._params:
        if param.name in overrides:
            if param.type is not None:
                check_type(overrides[param.name], param.type)
            config[param.name] = overrides[param.name]
        else:
            config[param.name] = param.default
    config['implement'] = implement
    return config

ConfigType = TypeVar('ConfigType')
def assemble_config(
    config: ConfigType,
    tools: list[Type[FactoryMixin]],
    override_yaml: Union[str, Path],
) -> ConfigType:
    if isinstance(override_yaml, str):
        override_yaml = Path(override_yaml)
    if override_yaml.exists():
        with override_yaml.open("r", encoding="utf-8") as file:
            overrides = yaml.safe_load(file) or {} or {}
        if not isinstance(overrides, dict):
            raise TypeError("Config override file must contain a YAML mapping")
        parsed_overrides = parse_env_variable(overrides)
        for tool in tools:
            tool_name = tool.__name__
            tool_overrides = parsed_overrides.pop(tool_name, {})
            tool_config = build_tool_config(tool, overrides=tool_overrides)
            setattr(config, tool_name, tool_config)
        override_config(config, parsed_overrides)
    return config
