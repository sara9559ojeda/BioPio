"""Ecological knowledge base used to build hypotheses.

The plausibility table encodes expert knowledge (how sensitive each group is
to each driver). It is deliberately explicit so researchers can review and
adjust it: it is an assumption of the model, not a measured fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from bioguard_svc.shared import scenario

AMPHIBIAN, BIRD, INSECT = scenario.AMPHIBIAN, scenario.BIRD, scenario.INSECT

PLAUSIBILITY: dict[int, dict[str, float]] = {
    AMPHIBIAN: {
        "WATER_WARMING": 0.90, "RIPARIAN_VEGETATION_LOSS": 0.85, "RAINFALL_DEFICIT": 0.80,
        "HUMIDITY_DROP": 0.75, "ANTHROPOGENIC_NOISE": 0.35, "AIR_HEAT": 0.55, "FIRE": 0.65,
        "HABITAT_CLEARING": 0.80, "LIVESTOCK_DISTURBANCE": 0.80,
    },
    BIRD: {
        "WATER_WARMING": 0.20, "RIPARIAN_VEGETATION_LOSS": 0.50, "RAINFALL_DEFICIT": 0.40,
        "HUMIDITY_DROP": 0.30, "ANTHROPOGENIC_NOISE": 0.85, "AIR_HEAT": 0.40, "FIRE": 0.70,
        "HABITAT_CLEARING": 0.75, "LIVESTOCK_DISTURBANCE": 0.40,
    },
    INSECT: {
        "WATER_WARMING": 0.25, "RIPARIAN_VEGETATION_LOSS": 0.50, "RAINFALL_DEFICIT": 0.55,
        "HUMIDITY_DROP": 0.55, "ANTHROPOGENIC_NOISE": 0.20, "AIR_HEAT": 0.60, "FIRE": 0.65,
        "HABITAT_CLEARING": 0.60, "LIVESTOCK_DISTURBANCE": 0.35,
    },
}


@dataclass(frozen=True)
class VariableDriver:
    driver: str
    # +1: the variable going UP is the threat; -1: going DOWN is the threat.
    direction: int
    label: str
    unit: str


VARIABLE_DRIVERS: dict[str, VariableDriver] = {
    scenario.WATER_TEMPERATURE: VariableDriver("WATER_WARMING", +1, "La temperatura del agua", "°C"),
    scenario.NDVI: VariableDriver("RIPARIAN_VEGETATION_LOSS", -1, "El NDVI ribereño", ""),
    scenario.RAINFALL: VariableDriver("RAINFALL_DEFICIT", -1, "La precipitación", "mm/día"),
    scenario.RELATIVE_HUMIDITY: VariableDriver("HUMIDITY_DROP", -1, "La humedad relativa", "%"),
    scenario.NOISE_LEVEL: VariableDriver("ANTHROPOGENIC_NOISE", +1, "El ruido antrópico", "dB"),
    scenario.AIR_TEMPERATURE: VariableDriver("AIR_HEAT", +1, "La temperatura del aire", "°C"),
}

EVENT_DRIVERS = {
    "FIRE": "FIRE",
    "CLEARING": "HABITAT_CLEARING",
    "LIVESTOCK": "LIVESTOCK_DISTURBANCE",
    "NOISE_SOURCE": "ANTHROPOGENIC_NOISE",
}


@dataclass(frozen=True)
class DriverText:
    title: str
    statement: str  # {species} placeholder
    rationale: str


DRIVER_TEXT: dict[str, DriverText] = {
    "WATER_WARMING": DriverText(
        "Calentamiento del agua de la quebrada",
        "El aumento de la temperatura del agua podría estar reduciendo la actividad de canto y reproducción de {species}.",
        "Los anfibios de quebrada dependen de agua fría y bien oxigenada para reproducirse.",
    ),
    "RIPARIAN_VEGETATION_LOSS": DriverText(
        "Pérdida de vegetación ribereña",
        "La caída del NDVI en la franja ribereña sugiere pérdida de cobertura, que podría afectar los sitios de canto y de puesta de {species}.",
        "Muchas ranas de quebrada (p. ej. ranas de cristal) ponen sus huevos en hojas sobre el agua y necesitan sombra.",
    ),
    "RAINFALL_DEFICIT": DriverText(
        "Déficit de lluvia",
        "Una disminución de la lluvia podría haber reducido la actividad de {species}.",
        "La actividad de anfibios e insectos aumenta con la lluvia.",
    ),
    "HUMIDITY_DROP": DriverText(
        "Descenso de la humedad del sotobosque",
        "La menor humedad relativa podría estar limitando la actividad de {species}.",
        "La piel de los anfibios es permeable: la desecación limita su actividad nocturna.",
    ),
    "ANTHROPOGENIC_NOISE": DriverText(
        "Ruido antrópico",
        "Un aumento del ruido podría enmascarar las vocalizaciones o desplazar a {species}.",
        "El ruido de baja frecuencia enmascara cantos y reduce la detectabilidad y el éxito reproductivo.",
    ),
    "AIR_HEAT": DriverText(
        "Calor atmosférico",
        "Temperaturas del aire más altas de lo habitual podrían estar alterando la actividad de {species}.",
        "La actividad de ectotermos depende de la temperatura ambiente.",
    ),
    "FIRE": DriverText(
        "Efecto de un incendio cercano",
        "El incendio reportado podría haber degradado el hábitat de {species}.",
        "Los incendios eliminan cobertura, aumentan la sedimentación y alteran el microclima.",
    ),
    "HABITAT_CLEARING": DriverText(
        "Tala o apertura del hábitat",
        "La apertura de cobertura reportada podría haber reducido el hábitat disponible para {species}.",
        "La fragmentación reduce el hábitat y aumenta el efecto de borde.",
    ),
    "LIVESTOCK_DISTURBANCE": DriverText(
        "Perturbación por ganado",
        "El ingreso de ganado a la ronda podría estar dañando los sitios de reproducción de {species}.",
        "El pisoteo y la sedimentación degradan las pozas donde se reproducen los anfibios.",
    ),
    "REGIONAL_OR_SEASONAL": DriverText(
        "Variación regional o estacional",
        "El cambio de {species} podría ser parte de una variación regional o estacional y no un problema local.",
        "Las poblaciones fluctúan con el clima; por eso se compara con una estación de referencia.",
    ),
    "SENSOR_OR_EFFORT": DriverText(
        "Falla del sensor o cambio en el esfuerzo de muestreo",
        "La caída aparente de {species} podría deberse a un problema del grabador y no a un cambio ecológico.",
        "Un micrófono degradado reduce las detecciones de todas las especies por igual.",
    ),
}

VERIFICATION_TASKS: dict[str, list[tuple[str, str, float]]] = {
    "WATER_WARMING": [
        ("Medir temperatura, oxígeno disuelto y conductividad del agua en 3 puntos (aguas arriba, estación, aguas abajo).",
         "Sonda multiparamétrica, 2 lecturas diarias durante 5 días", 16.0),
        ("Verificar captaciones o desvíos de agua aguas arriba.", "Recorrido de la quebrada y entrevista con propietarios", 6.0),
    ],
    "RIPARIAN_VEGETATION_LOSS": [
        ("Medir la cobertura ribereña en 100 m a cada margen de la quebrada.", "Parcelas de 10×10 m cada 20 m con densiómetro", 12.0),
        ("Contrastar con imágenes satelitales de mayor resolución.", "Comparación con la línea base de cobertura", 3.0),
    ],
    "RAINFALL_DEFICIT": [
        ("Comparar el pluviómetro local con la estación meteorológica más cercana.", "Revisión de series diarias", 2.0),
    ],
    "HUMIDITY_DROP": [
        ("Instalar registradores de humedad en el sotobosque junto a la quebrada.", "Dataloggers cada 15 min durante 2 semanas", 6.0),
    ],
    "ANTHROPOGENIC_NOISE": [
        ("Localizar la fuente de ruido y medir su nivel y horario.", "Recorrido con sonómetro en horario diurno y nocturno", 8.0),
    ],
    "AIR_HEAT": [
        ("Revisar el blindaje solar del sensor de temperatura y comparar con un sensor de referencia.", "Inspección + lecturas pareadas por 3 días", 4.0),
    ],
    "FIRE": [
        ("Delimitar el área quemada y su conexión con la quebrada.", "Recorrido con GPS y fotografías", 10.0),
        ("Evaluar sedimentación en las pozas de reproducción.", "Inspección visual y medición de turbidez", 6.0),
    ],
    "HABITAT_CLEARING": [
        ("Confirmar en campo el área intervenida y su distancia a la estación.", "Recorrido con GPS", 6.0),
    ],
    "LIVESTOCK_DISTURBANCE": [
        ("Buscar evidencias de pisoteo, heces y ramoneo en la ronda.", "Transecto de 200 m por la ronda", 5.0),
    ],
    "REGIONAL_OR_SEASONAL": [
        ("Revisar registros históricos del mismo mes y estaciones vecinas.", "Análisis de series multianuales", 3.0),
    ],
    "SENSOR_OR_EFFORT": [
        ("Inspeccionar el grabador: micrófono, carcasa y calibración.", "Visita técnica + clip de calibración", 4.0),
        ("Escuchar una muestra aleatoria de clips para validar el modelo.", "Revisión manual de 20 clips", 3.0),
    ],
}

MECHANISM_LINKS: list[tuple[str, str, str]] = [
    ("RIPARIAN_VEGETATION_LOSS", "WATER_WARMING",
     "La pérdida de sombra ribereña puede elevar la temperatura del agua: ambas hipótesis podrían ser parte del mismo mecanismo."),
    ("RIPARIAN_VEGETATION_LOSS", "HUMIDITY_DROP", "Menos cobertura ribereña reduce la humedad del sotobosque."),
    ("FIRE", "RIPARIAN_VEGETATION_LOSS", "Un incendio puede eliminar vegetación ribereña."),
    ("HABITAT_CLEARING", "RIPARIAN_VEGETATION_LOSS", "La tala reduce la cobertura ribereña."),
    ("LIVESTOCK_DISTURBANCE", "RIPARIAN_VEGETATION_LOSS", "El ganado ramonea y pisotea la vegetación de la ronda."),
]

CAVEAT = (
    "BioGuard no atribuye una causa. Las hipótesis están ordenadas por el respaldo de los datos "
    "disponibles (no son probabilidades de causa); solo el trabajo de campo puede confirmarlas o descartarlas."
)
