from typing import Any, Dict, Optional, Type, TypeVar

Product = TypeVar('Product', bound='FactoryMixin')
class FactoryMixin:
    
    _implements: Dict[str, Type[Any]]
    implement: Optional[str]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._implements = {}
        if not hasattr(cls, 'implement'):
            raise AttributeError("FactoryMixin subclass must define an 'implement' attribute")

    @classmethod
    def register_impl(cls):
        def decorator(product_cls: Type[Product]) -> Type[Product]:
            cls._implements[product_cls.__name__] = product_cls
            return product_cls
        return decorator
    
    @classmethod
    def get_impl(cls, name: str) -> Type[Product]:
        try:
            return cls._implements[name]
        except KeyError as exc:
            available = ", ".join(sorted(cls._implements))
            raise ValueError(
                f"Unknown implementation {name!r} for {cls.__name__}. "
                f"Available: {available}"
            ) from exc
    
    @classmethod
    def build(cls, config):
        raw_config = dict(getattr(config, cls.__name__))
        implement = raw_config.pop('implement')
        impl_cls = cls.get_impl(implement)
        return impl_cls(**raw_config)
