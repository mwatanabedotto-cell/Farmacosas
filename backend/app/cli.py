"""Comandos de mantenimiento.

    python -m app.cli sembrar [--csv RUTA]
    python -m app.cli sincronizar-openfda [--dci enoxaparina ...]
    python -m app.cli importar-fichas [--dci enoxaparina ...]
"""

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import select

from app.connectors.openfda import OpenFDAClient
from app.core.config import get_settings
from app.db import SessionLocal
from app.models import PrincipioActivo
from app.services import fichas, sync_openfda
from app.services.semilla import cargar_principios


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="farmacosas")
    sub = parser.add_subparsers(dest="comando", required=True)
    p_sembrar = sub.add_parser("sembrar", help="Carga los principios activos del CSV semilla")
    p_sembrar.add_argument("--csv", type=Path, default=None)
    p_sync = sub.add_parser("sincronizar-openfda", help="Descarga productos de EE. UU. desde openFDA")
    p_sync.add_argument("--dci", nargs="*", help="DCI en español (por defecto, todos)")
    p_fichas = sub.add_parser("importar-fichas", help="Importa fichas técnicas de DailyMed y genera borradores")
    p_fichas.add_argument("--dci", nargs="*", help="DCI en español (por defecto, todos)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()

    with SessionLocal() as db:
        if args.comando == "sembrar":
            creados, actualizados = cargar_principios(db, args.csv or settings.csv_principios)
            print(f"Principios activos: {creados} creados, {actualizados} actualizados")
            return 0

        ids = None
        if args.dci:
            principios = db.scalars(select(PrincipioActivo).where(PrincipioActivo.dci_es.in_(args.dci))).all()
            faltan = set(args.dci) - {p.dci_es for p in principios}
            if faltan:
                print(f"No encontrados: {', '.join(sorted(faltan))}", file=sys.stderr)
                return 1
            ids = [p.id for p in principios]
        cliente = OpenFDAClient(
            api_key=settings.openfda_api_key,
            max_resultados=settings.openfda_max_resultados,
            pausa_segundos=settings.openfda_pausa_segundos,
        )
        errores = 0
        if args.comando == "importar-fichas":
            for s in fichas.importar_fichas(db, cliente, ids):
                print(f"{s.principio_activo.dci_es:35} {s.estado:6} {s.mensaje}")
                errores += s.estado == "error"
            return 1 if errores else 0
        for s in sync_openfda.sincronizar(db, cliente, ids):
            nombre = s.principio_activo.dci_es if s.principio_activo else "?"
            print(
                f"{nombre:35} {s.estado:9} creados={s.n_creados} actualizados={s.n_actualizados} "
                f"sin_cambios={s.n_sin_cambios} no_listados={s.n_no_listados}"
                + (f"  [{s.mensaje}]" if s.mensaje else "")
            )
            errores += s.estado == "error"
        return 1 if errores else 0


if __name__ == "__main__":
    raise SystemExit(main())
