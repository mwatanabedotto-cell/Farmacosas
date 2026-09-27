"""Comandos de mantenimiento.

    python -m app.cli sembrar [--csv RUTA]
    python -m app.cli sincronizar-openfda [--dci enoxaparina ...]
    python -m app.cli importar-fichas [--dci enoxaparina ...]
    python -m app.cli sincronizar-cima [--dci enoxaparina ...]
    python -m app.cli importar-fichas-cima [--dci enoxaparina ...]
    python -m app.cli sincronizar-invima [--dci enoxaparina ...]
    python -m app.cli sincronizar-liname
"""

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import select

from app.connectors.agemed import AgemedClient
from app.connectors.cima import CimaClient, ConectorCima
from app.connectors.invima import ConectorInvima, InvimaClient
from app.connectors.openfda import OpenFDAClient
from app.core.config import get_settings
from app.db import SessionLocal
from app.models import PrincipioActivo
from app.services import fichas, fichas_cima, listas_esenciales, sincronizacion, sync_openfda
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
    p_cima = sub.add_parser("sincronizar-cima", help="Descarga medicamentos de España desde CIMA (AEMPS)")
    p_cima.add_argument("--dci", nargs="*", help="DCI en español (por defecto, todos)")
    p_fcima = sub.add_parser("importar-fichas-cima", help="Importa fichas técnicas de CIMA y actualiza borradores")
    p_fcima.add_argument("--dci", nargs="*", help="DCI en español (por defecto, todos)")
    p_invima = sub.add_parser("sincronizar-invima", help="Descarga medicamentos de Colombia (CUM de INVIMA)")
    p_invima.add_argument("--dci", nargs="*", help="DCI en español (por defecto, todos)")
    sub.add_parser("sincronizar-liname", help="Descarga la LINAME de Bolivia (AGEMED)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()

    with SessionLocal() as db:
        if args.comando == "sembrar":
            creados, actualizados = cargar_principios(db, args.csv or settings.csv_principios)
            print(f"Principios activos: {creados} creados, {actualizados} actualizados")
            return 0

        if args.comando == "sincronizar-liname":
            s = listas_esenciales.sincronizar_liname(db, AgemedClient(pausa_segundos=settings.agemed_pausa_segundos))
            print(f"LINAME {s.estado}: creadas={s.n_creados} actualizadas={s.n_actualizados} "
                  f"sin_cambios={s.n_sin_cambios} excluidas={s.n_no_listados}  [{s.mensaje}]")
            return 0 if s.estado == "ok" else 1

        ids = None
        if getattr(args, "dci", None):
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
        cima = CimaClient(pausa_segundos=settings.cima_pausa_segundos)
        if args.comando in ("importar-fichas", "importar-fichas-cima"):
            ejecuciones = (
                fichas.importar_fichas(db, cliente, ids) if args.comando == "importar-fichas"
                else fichas_cima.importar_fichas(db, cima, ids)
            )
            for s in ejecuciones:
                print(f"{s.principio_activo.dci_es:35} {s.estado:6} {s.mensaje}")
                errores += s.estado == "error"
            return 1 if errores else 0
        if args.comando == "sincronizar-cima":
            ejecuciones = sincronizacion.sincronizar(db, ConectorCima(cima), ids)
        elif args.comando == "sincronizar-invima":
            invima = InvimaClient(app_token=settings.datosgov_app_token, pausa_segundos=settings.invima_pausa_segundos)
            ejecuciones = sincronizacion.sincronizar(db, ConectorInvima(invima), ids)
        else:
            ejecuciones = sync_openfda.sincronizar(db, cliente, ids)
        for s in ejecuciones:
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
