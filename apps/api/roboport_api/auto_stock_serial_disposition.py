"""Общая проверка явного списка идентификаторов в складском акте."""
from uuid import UUID
from .auto_stock_service import AutoStockDenied


def normalize_serials(serials,quantity):
    if (not isinstance(serials,(tuple,list)) or len(serials)>50
        or (len(serials)>0 and len(serials)!=quantity)
        or any(type(value) is not UUID for value in serials)
        or len(set(serials))!=len(serials)):
        raise AutoStockDenied("INVALID_INPUT")
    return sorted(serials,key=str)
