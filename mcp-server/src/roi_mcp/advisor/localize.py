"""Language support for advisor and knowledge responses (phase-2 addendum section 5).

Game names come from the static catalogue only (display_name in the game's language, english_name); they are never
machine-translated. Advisor-authored texts (summaries, suggestions, notes) come from this fixed catalogue: every
English sentence the advisor emits has an authored French version. Ids, enums, units and numbers are never changed.
"""

from __future__ import annotations

import re
from typing import Any

TEXT_KEYS = ("summary", "suggestion", "note")

# Exact advisor sentences (English -> French).
FR = {
    # diagnose_chain
    "No recipe produces this product; it cannot be manufactured.": "Aucune recette ne produit ce produit : il ne peut pas être fabriqué.",
    "The company has no building producing this product.": "L'entreprise n'a aucun bâtiment qui produit ce produit.",
    "Every producer of this product is disabled.": "Tous les producteurs de ce produit sont désactivés.",
    "Producer idle for lack of an input.": "Producteur à l'arrêt faute d'un intrant.",
    "Producer's output storage is full: production stalls until stock leaves.":
        "Le stockage de sortie du producteur est plein : la production s'arrête tant que le stock ne part pas.",
    "Producer is polluted.": "Le producteur est pollué.",
    "Output storage is at least 90 % full (D-INV-1).": "Le stockage de sortie est plein à au moins 90 % (D-INV-1).",
    "The company's internal need exceeds its theoretical supply of this product (estimate).":
        "Le besoin interne de l'entreprise dépasse son offre théorique de ce produit (estimation).",
    "No route or warehouse request brings this input to the building.":
        "Aucune route ni demande d'entrepôt n'apporte cet intrant au bâtiment.",
    "Every inbound route for this input is paused, errored, dormant or keep-all.":
        "Toutes les routes entrantes pour cet intrant sont en pause, en erreur, inactives ou en « tout garder ».",
    "Every inbound route for this input would dispatch 0 units now.":
        "Toutes les routes entrantes pour cet intrant enverraient 0 unité maintenant.",
    "No configured route ships this product out of its producers.":
        "Aucune route configurée n'expédie ce produit depuis ses producteurs.",
    "Output piles up at the producers while shops still have unmet demand: outbound logistics limit sales.":
        "La production s'accumule chez les producteurs alors que des magasins ont une demande non satisfaite : la logistique sortante limite les ventes.",
    "Theoretical supply exceeds internal need plus the shop demand the company sees (estimate).":
        "L'offre théorique dépasse le besoin interne plus la demande des magasins vue par l'entreprise (estimation).",
    "Producer stopped: blocked.": "Producteur arrêté : bloqué.",
    "Producer stopped: no_modules.": "Producteur arrêté : aucun module.",
    "Producer stopped: deposit_depleted.": "Producteur arrêté : gisement épuisé.",
    # get_overview attention
    "Cash is negative: the game checks bankruptcy at month end.":
        "La trésorerie est négative : le jeu vérifie la faillite en fin de mois.",
    "The last complete month ended with a net loss.": "Le dernier mois complet s'est terminé sur une perte nette.",
    "Internal need for this product exceeds the company's theoretical supply (estimate).":
        "Le besoin interne de ce produit dépasse l'offre théorique de l'entreprise (estimation).",
    "Some routes report errors (e.g. no path).": "Certaines routes signalent des erreurs (par ex. aucun chemin).",
    "Some routes keep all stock and never dispatch.": "Certaines routes gardent tout le stock et n'expédient jamais.",
    "No research is active or queued.": "Aucune recherche n'est active ni en file d'attente.",
    # route findings
    "Check the route in the game: the destination may be unreachable (no path) or invalid.":
        "Vérifiez la route dans le jeu : la destination est peut-être inaccessible (aucun chemin) ou invalide.",
    "Min Keep is set to keep everything: this route never dispatches. Lower Min Keep if it should ship.":
        "Min Keep est réglé pour tout garder : cette route n'expédie jamais. Baissez Min Keep si elle doit livrer.",
    "The destination does not accept this product: deliveries cannot be stored there.":
        "La destination n'accepte pas ce produit : les livraisons ne peuvent pas y être stockées.",
    "The destination's city is dead: shop demand is 0.": "La ville de destination est morte : la demande des magasins est nulle.",
    "The route is paused in the game.": "La route est en pause dans le jeu.",
    "AUTO_WH is set on the origin: its manual routes are dormant while the building pushes to its warehouse.":
        "AUTO_WH est activé sur l'origine : ses routes manuelles sont inactives pendant que le bâtiment livre son entrepôt.",
    "The next dispatch would request 0 units now because the Max Send room is used by stock at the destination "
    "and deliveries already on the way. This is normal while vehicles are in transit; it is a problem only "
    "if it persists.":
        "Le prochain envoi demanderait 0 unité maintenant, car la marge de Max Send est occupée par le stock de la "
        "destination et les livraisons déjà en route. C'est normal tant que des véhicules sont en transit ; ce n'est "
        "un problème que si cela dure.",
    "The next dispatch would request 0 units now; see limited_by (origin stock vs Min Keep, destination space, Max Send).":
        "Le prochain envoi demanderait 0 unité maintenant ; voir limited_by (stock d'origine vs Min Keep, place à destination, Max Send).",
    "The destination has reached the Max Send cap (stock + incoming). The cap is shared by every origin shipping this product there.":
        "La destination a atteint le plafond Max Send (stock + arrivages). Ce plafond est partagé par toutes les origines qui y livrent ce produit.",
    "Transport costs a large share of the product's value on this route; a closer destination or a larger vehicle may be cheaper.":
        "Le transport coûte une grande part de la valeur du produit sur cette route ; une destination plus proche ou un véhicule plus grand peut coûter moins.",
    "The next dispatch would leave less than half the vehicle used; each dispatch costs the same.":
        "Le prochain envoi utiliserait moins de la moitié du véhicule ; chaque envoi coûte le même prix.",
    "Several origins ship this product to the same destination; changing Max Send on one changes it for all.":
        "Plusieurs origines livrent ce produit à la même destination ; changer Max Send sur l'une le change pour toutes.",
    "Another slot of this origin ships the same product to the same destination.":
        "Un autre emplacement de cette origine livre le même produit à la même destination.",
    # opportunities
    "Spare supply exists while shops still have unmet demand: route more of it to shops.":
        "Il reste de l'offre disponible alors que des magasins ont une demande non satisfaite : envoyez-en davantage aux magasins.",
    "Shop demand exceeds the company's spare supply: more producers could serve it.":
        "La demande des magasins dépasse l'offre disponible de l'entreprise : davantage de producteurs pourraient la servir.",
    "Unmet shop demand for a product the company does not make yet (unlocked).":
        "Demande non satisfaite pour un produit que l'entreprise ne fabrique pas encore (débloqué).",
    "Unmet shop demand for a product whose recipe or building is still locked.":
        "Demande non satisfaite pour un produit dont la recette ou le bâtiment est encore verrouillé.",
    "A city offers a delivery contract (observed terms); its profitability is not estimated.":
        "Une ville propose un contrat de livraison (conditions observées) ; sa rentabilité n'est pas estimée.",
    # notes and side effects
    "Hypothetical: computed on copies of the current snapshot. Nothing was changed in the game.":
        "Hypothèse : calculée sur des copies de l'instantané actuel. Rien n'a été modifié dans le jeu.",
    "Complete months only; the in-progress month is never compared as if complete.":
        "Mois complets uniquement ; le mois en cours n'est jamais comparé comme s'il était complet.",
    "balance = supply - internal need - demand of every live shop in the world; a deficit is mostly "
    "unserved market demand, not an input shortage (those are attention items input_deficit)":
        "solde = production - besoin interne - demande de tous les magasins actifs du monde ; un déficit est surtout "
        "une demande de marché non servie, pas une pénurie d'intrant (celles-ci sont les points d'attention input_deficit)",
    "Max Send is stored on the destination and shared by every origin shipping this product there":
        "Max Send est stocké sur la destination et partagé par toutes les origines qui y livrent ce produit",
    "routes share one Max Send room; the first origin to dispatch uses it up, so per-route amounts do not add up "
    "beyond the room (dispatch order and timing are not exported)":
        "les routes partagent la même marge de Max Send ; la première origine qui expédie la consomme, les quantités "
        "par route ne s'additionnent donc pas au-delà de cette marge (l'ordre et le moment des envois ne sont pas exportés)",
    "changing the recipe clears stock of products the new recipe does not use (mechanic storage-per-product)":
        "changer de recette vide le stock des produits que la nouvelle recette n'utilise pas (mécanique storage-per-product)",
    "higher efficiency levels may require a technology": "les niveaux d'efficacité supérieurs peuvent exiger une technologie",
    "per_month = per 30 game days (the game has 30-day months).": "per_month = par 30 jours de jeu (le jeu a des mois de 30 jours).",
    "this route uses auto Max Send (shop demand); a manual value applies only after turning auto off":
        "cette route utilise Max Send automatique (demande du magasin) ; une valeur manuelle ne s'applique qu'après avoir désactivé l'auto",
}

# Sentences with variable parts: (regex on the English text, French template using the groups).
FR_PATTERNS = [
    (re.compile(r"^(\d+) building\(s\) with status (\w+)\.$"), "{0} bâtiment(s) avec le statut {1}."),
    (re.compile(r"^(\w+) loans are granted by game events, not freely taken$"),
     "les prêts {0} sont accordés par des événements du jeu, pas librement"),
]


def to_french(text: str) -> str | None:
    if text in FR:
        return FR[text]
    for rx, tpl in FR_PATTERNS:
        m = rx.match(text)
        if m:
            return tpl.format(*m.groups())
    return None


def catalog_is_french(static_language: Any) -> bool:
    return isinstance(static_language, str) and static_language.strip().lower() in ("french", "fr", "fr-fr", "français", "francais")


def _name_fields(obj: dict, language: str, french_catalog: bool) -> None:
    en = obj.get("english_name") or (obj["id"].split(":", 1)[1] if isinstance(obj.get("id"), str) and ":" in obj["id"] else None)
    en_source = "catalogue_en" if obj.get("english_name") else "asset"
    fr = obj.get("display_name") if french_catalog else None
    if language == "en":
        obj["name"], obj["name_source"] = en, en_source
    elif language == "fr":
        if fr:
            obj["name"], obj["name_source"] = fr, "catalogue_fr"
        else:
            obj["name"], obj["name_source"] = en, "fallback_en"
    else:
        obj["name"], obj["name_source"] = en, en_source
        obj["name_fr"] = fr
        obj["name_fr_source"] = "catalogue_fr" if fr else "unavailable"


def localize(obj: Any, language: str, french_catalog: bool) -> Any:
    """Apply the language in place to advisor-authored texts and catalogue name references."""
    if language not in ("en", "fr", "both"):
        return obj
    stack = [obj]
    missing = 0
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if isinstance(o.get("id"), str) and ("english_name" in o and "display_name" in o):
                _name_fields(o, language, french_catalog)
            if isinstance(o.get("title"), str) and isinstance(o.get("title_fr"), str):
                if language == "fr":
                    o["title"] = o["title_fr"]
                elif language == "en":
                    o.pop("title_fr")
            for k in TEXT_KEYS:
                if isinstance(o.get(k), str) and language != "en":
                    fr = to_french(o[k])
                    if fr is None:
                        missing += 1
                        o[f"{k}_language"] = "en"
                    elif language == "fr":
                        o[k] = fr
                    else:
                        o[f"{k}_fr"] = fr
            stack.extend(v for v in o.values() if isinstance(v, (dict, list)))
        elif isinstance(o, list):
            stack.extend(v for v in o if isinstance(v, (dict, list)))
    return missing
