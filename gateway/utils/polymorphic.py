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
        return cls._implements[name]
    
    @classmethod
    def build(cls, config):
        config = getattr(config, cls.__name__)
        cls = cls.get_impl(config.pop('implement'))
        return cls(**config)