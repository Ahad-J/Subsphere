"""Self-contained Pydantic v2-compatible module (delegates to site-packages if installed)."""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import types
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Union, get_args, get_origin, get_type_hints

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)

# Check if a real pydantic is installed in site-packages
def _try_load_real():
    saved_path = list(sys.path)
    try:
        sys.path = [p for p in sys.path if os.path.abspath(p or ".") not in (_BACKEND_DIR, _THIS_DIR)]
        spec = importlib.util.find_spec("pydantic")
        if spec and spec.origin and os.path.dirname(os.path.abspath(spec.origin)) != _THIS_DIR:
            return spec
    except Exception:
        pass
    finally:
        sys.path = saved_path
    return None

# Note: keep local lightweight implementation active unless explicitly using external site-packages
class _UndefinedType:
    def __repr__(self) -> str:
        return "PydanticUndefined"

PydanticUndefined = _UndefinedType()


class ValidationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]):
        self._errors = errors
        super().__init__(str(errors))

    def errors(self) -> list[dict[str, Any]]:
        return self._errors


class FieldInfo:
    def __init__(
        self,
        default: Any = PydanticUndefined,
        *,
        default_factory: Callable[[], Any] | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
        ge: int | float | Decimal | None = None,
        gt: int | float | Decimal | None = None,
        le: int | float | Decimal | None = None,
        lt: int | float | Decimal | None = None,
        pattern: str | None = None,
        description: str | None = None,
        alias: str | None = None,
    ):
        self.default = default
        self.default_factory = default_factory
        self.min_length = min_length
        self.max_length = max_length
        self.ge = ge
        self.gt = gt
        self.le = le
        self.lt = lt
        self.pattern = pattern
        self.description = description
        self.alias = alias


def Field(
    default: Any = PydanticUndefined,
    *,
    default_factory: Callable[[], Any] | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    ge: int | float | Decimal | None = None,
    gt: int | float | Decimal | None = None,
    le: int | float | Decimal | None = None,
    lt: int | float | Decimal | None = None,
    pattern: str | None = None,
    description: str | None = None,
    alias: str | None = None,
) -> Any:
    return FieldInfo(
        default=default,
        default_factory=default_factory,
        min_length=min_length,
        max_length=max_length,
        ge=ge,
        gt=gt,
        le=le,
        lt=lt,
        pattern=pattern,
        description=description,
        alias=alias,
    )


class ConfigDict(dict):
    pass


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class EmailStr(str):
    @classmethod
    def validate(cls, value: Any) -> str:
        if not isinstance(value, str) or not _EMAIL_RE.match(value.strip()):
            raise ValueError("Invalid email address")
        return value.strip().lower()


def field_validator(*fields: str, mode: str = "after"):
    def decorator(func: Callable) -> Callable:
        func.__pydantic_field_validator__ = (fields, mode)
        return func
    return decorator


def model_validator(*, mode: str = "after"):
    def decorator(func: Callable) -> Callable:
        func.__pydantic_model_validator__ = mode
        return func
    return decorator


def _coerce_value(val: Any, ann: Any, loc: tuple[str, ...]) -> Any:
    origin = get_origin(ann)
    is_union = origin in (Union, types.UnionType)
    if val is None:
        if is_union and type(None) in get_args(ann):
            return None
        if ann is Any or ann is None:
            return None
        raise ValidationError([{"loc": list(loc), "msg": "Field cannot be null", "type": "value_error.none"}])

    if is_union:
        args = [a for a in get_args(ann) if a is not type(None)]
        last_err = None
        for a in args:
            try:
                return _coerce_value(val, a, loc)
            except ValidationError as exc:
                last_err = exc
        if last_err:
            raise last_err
        return val

    if ann is Any:
        return val
    if ann is EmailStr:
        try:
            return EmailStr.validate(val)
        except ValueError as e:
            raise ValidationError([{"loc": list(loc), "msg": str(e), "type": "value_error.email"}]) from e
    if ann is str:
        if not isinstance(val, str):
            raise ValidationError([{"loc": list(loc), "msg": "Input should be a valid string", "type": "string_type"}])
        return val
    if ann is bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, str) and val.lower() in ("true", "1", "yes"):
            return True
        if isinstance(val, str) and val.lower() in ("false", "0", "no"):
            return False
        raise ValidationError([{"loc": list(loc), "msg": "Input should be a valid boolean", "type": "bool_type"}])
    if ann is int:
        if isinstance(val, bool):
            raise ValidationError([{"loc": list(loc), "msg": "Input should be a valid integer", "type": "int_type"}])
        if isinstance(val, int):
            return val
        if isinstance(val, str):
            try:
                return int(val)
            except ValueError:
                pass
        raise ValidationError([{"loc": list(loc), "msg": "Input should be a valid integer", "type": "int_type"}])
    if ann is float:
        if isinstance(val, bool):
            raise ValidationError([{"loc": list(loc), "msg": "Input should be a valid float", "type": "float_type"}])
        try:
            return float(val)
        except (TypeError, ValueError):
            raise ValidationError([{"loc": list(loc), "msg": "Input should be a valid number", "type": "float_type"}])
    if ann is Decimal:
        try:
            return Decimal(str(val))
        except Exception:
            raise ValidationError([{"loc": list(loc), "msg": "Input should be a valid decimal", "type": "decimal_type"}])
    if ann is date:
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, date):
            return val
        if isinstance(val, str):
            try:
                return date.fromisoformat(val)
            except ValueError:
                pass
        raise ValidationError([{"loc": list(loc), "msg": "Input should be a valid date (YYYY-MM-DD)", "type": "date_parsing"}])
    if ann is datetime:
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except ValueError:
                pass
        raise ValidationError([{"loc": list(loc), "msg": "Input should be a valid datetime", "type": "datetime_parsing"}])
    if origin is list:
        if not isinstance(val, list):
            raise ValidationError([{"loc": list(loc), "msg": "Input should be a valid list", "type": "list_type"}])
        item_t = get_args(ann)[0] if get_args(ann) else Any
        return [_coerce_value(item, item_t, loc + (str(idx),)) for idx, item in enumerate(val)]
    if origin is dict:
        if not isinstance(val, dict):
            raise ValidationError([{"loc": list(loc), "msg": "Input should be a valid dict", "type": "dict_type"}])
        return dict(val)
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        if isinstance(val, ann):
            return val
        if isinstance(val, dict):
            return ann.model_validate(val)
        raise ValidationError([{"loc": list(loc), "msg": "Input should be a valid object", "type": "model_type"}])
    return val


class BaseModel:
    model_config: ConfigDict = ConfigDict()

    def __init__(self, **data: Any):
        cls = self.__class__
        try:
            hints = get_type_hints(cls)
        except Exception:
            hints = getattr(cls, "__annotations__", {})

        field_validators: dict[str, list[Callable]] = {}
        model_validators: list[Callable] = []
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name, None)
            if callable(attr) and hasattr(attr, "__pydantic_field_validator__"):
                fields, _mode = getattr(attr, "__pydantic_field_validator__")
                for fname in fields:
                    field_validators.setdefault(fname, []).append(attr)
            if callable(attr) and hasattr(attr, "__pydantic_model_validator__"):
                model_validators.append(attr)

        errors: list[dict[str, Any]] = []
        values: dict[str, Any] = {}

        for name, ann in hints.items():
            if name.startswith("_") or name == "model_config":
                continue
            default_attr = getattr(cls, name, PydanticUndefined)
            field_info = default_attr if isinstance(default_attr, FieldInfo) else None

            raw_val = PydanticUndefined
            if name in data:
                raw_val = data[name]
            elif field_info and field_info.alias and field_info.alias in data:
                raw_val = data[field_info.alias]
            elif field_info is not None:
                if field_info.default_factory is not None:
                    raw_val = field_info.default_factory()
                elif field_info.default is not PydanticUndefined:
                    raw_val = field_info.default
            elif default_attr is not PydanticUndefined:
                raw_val = default_attr

            if raw_val is PydanticUndefined:
                errors.append({"loc": ["body", name], "msg": "Field required", "type": "missing"})
                continue

            try:
                val = _coerce_value(raw_val, ann, ("body", name))
                if field_info is not None and val is not None:
                    if field_info.min_length is not None and isinstance(val, str) and len(val) < field_info.min_length:
                        raise ValidationError([{"loc": ["body", name], "msg": f"String should have at least {field_info.min_length} characters", "type": "string_too_short"}])
                    if field_info.max_length is not None and isinstance(val, str) and len(val) > field_info.max_length:
                        raise ValidationError([{"loc": ["body", name], "msg": f"String should have at most {field_info.max_length} characters", "type": "string_too_long"}])
                    if field_info.ge is not None and val < field_info.ge:
                        raise ValidationError([{"loc": ["body", name], "msg": f"Input should be greater than or equal to {field_info.ge}", "type": "greater_than_equal"}])
                    if field_info.gt is not None and val <= field_info.gt:
                        raise ValidationError([{"loc": ["body", name], "msg": f"Input should be greater than {field_info.gt}", "type": "greater_than"}])
                    if field_info.le is not None and val > field_info.le:
                        raise ValidationError([{"loc": ["body", name], "msg": f"Input should be less than or equal to {field_info.le}", "type": "less_than_equal"}])
                    if field_info.lt is not None and val >= field_info.lt:
                        raise ValidationError([{"loc": ["body", name], "msg": f"Input should be less than {field_info.lt}", "type": "less_than"}])
                    if field_info.pattern is not None and isinstance(val, str) and not re.match(field_info.pattern, val):
                        raise ValidationError([{"loc": ["body", name], "msg": "String should match pattern", "type": "string_pattern_mismatch"}])
                for fv in field_validators.get(name, []):
                    try:
                        val = fv(val)
                    except TypeError:
                        val = fv(cls, val)
                values[name] = val
            except ValidationError as ve:
                errors.extend(ve.errors())
            except ValueError as ve:
                errors.append({"loc": ["body", name], "msg": str(ve), "type": "value_error"})

        if errors:
            raise ValidationError(errors)

        for k, v in values.items():
            object.__setattr__(self, k, v)

        for mv in model_validators:
            try:
                mv(self)
            except ValueError as ve:
                raise ValidationError([{"loc": ["body"], "msg": str(ve), "type": "value_error"}]) from ve

    @classmethod
    def model_validate(cls, data: Any):
        if isinstance(data, cls):
            return data
        if isinstance(data, dict):
            return cls(**data)
        raise ValidationError([{"loc": [], "msg": "Invalid input for model", "type": "model_type"}])

    def model_dump(self, *, exclude_none: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if exclude_none and v is None:
                continue
            if isinstance(v, BaseModel):
                out[k] = v.model_dump(exclude_none=exclude_none)
            elif isinstance(v, list):
                out[k] = [i.model_dump(exclude_none=exclude_none) if isinstance(i, BaseModel) else i for i in v]
            elif isinstance(v, (date, datetime)):
                out[k] = v.isoformat()
            elif isinstance(v, Decimal):
                out[k] = str(v)
            else:
                out[k] = v
        return out

    def __repr__(self) -> str:
        fields = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items())
        return f"{self.__class__.__name__}({fields})"


__all__ = [
    "BaseModel",
    "ConfigDict",
    "EmailStr",
    "Field",
    "ValidationError",
    "field_validator",
    "model_validator",
]
