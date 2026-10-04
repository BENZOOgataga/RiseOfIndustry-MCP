# Company, Finances, Competitors, Shares, Loans, History, Game Time

> **Errata from the asset dump (2026-10-03, CONFIRMED, see `notes/static-dump.md`):**
> - `TimeManager.secondsPerDay` = **8.0**; `SpeedControls.speedLevels` = [1, 3, 6, 10] (game scene asset).
> - `MoneyAgent` prefabs: HumanPlayer and AiPlayer keep 3 years of history; **AiPlayer and State have `_infiniteMoney` = 1**, so AI cash is not meaningful.
> - `Upkeep.buildingCostPercentage` = 0.025 in prefabs (code default 0.25 quoted in §3.4).
> - Money bill category asset names and the full list of formula texts are in `notes/static-dump.md`.

Static-analysis notes for a future **read-only** MCP observer of the original *Rise of Industry*
(Steam 671440, Unity 2018.4.11 Mono, build 9064059). Not Rise of Industry 2.

Source: ILSpy output in `research/_local/decompiled/Assembly-CSharp/ProjectAutomata/` (namespace
`ProjectAutomata`). All `File.cs:NN` references are relative to that folder. `Assembly-CSharp-firstpass`
has nothing relevant to this topic (grep for money/loan/TimeManager/GameDate: no hits).

Labels:
- **CONFIRMED**: read directly in the decompiled source.
- **HIGH CONFIDENCE**: strongly implied by source, but one step of inference.
- **INFERRED**: reasoned from naming or usage. Not verified.
- **UNKNOWN**: depends on serialized prefab or asset data, or on Unity runtime behaviour that the C# does not show.

Many tunables (`secondsPerDay`, `speedLevels`, `historicalDataRange`, `keepsHistoricalData`, category asset
names) are serialized Unity fields. Their **runtime values are not in the C#** and must be read live.

---

## 0. TL;DR for the observer

| What | Where (entry point) | Status |
|---|---|---|
| Player company | `Player.humanPlayer` (static, `HumanPlayer`) | CONFIRMED `Player.cs:16` |
| "Currently viewed" company | `Player.activeActor` (can be switched by UI/debug) | CONFIRMED `Player.cs:14,45` |
| AI companies | `ManagerBehaviour<AiPlayerManager>.instance.aiPlayers` (`ReadOnlyList<AiPlayer>`) | CONFIRMED `AiPlayerManager.cs:32` |
| All actors (players, State, settlements) | `ManagerBehaviour<ActorManager>.instance.actors` / `GetActor(int id)` | CONFIRMED `ActorManager.cs:18,48` |
| Cash | `MoneyManager.instance.balances` (`Dictionary<IMoneyAgent,double>`), keyed by the actor's `money` agent | CONFIRMED `MoneyManager.cs:81` |
| Ledger | `IMoneyAgent.GetBills/GetIncome/GetExpenses/GetProfit(from,to[,category])` | CONFIRMED `MoneyAgent.cs:198-254` |
| Loans | `actor.loans.loans` (`List<Loan>`) | CONFIRMED `LoansAgent.cs:35` |
| Shares | `actor.Get<CompanySharesAgent>().bundles` (10 bundles × 0.1 each by default) | CONFIRMED `CompanySharesAgent.cs:11,24` |
| Company value | `actor.Get<CompanyStats>().ComputeCompanyValue()` | CONFIRMED `CompanyStats.cs:22` |
| Date | `ManagerBehaviour<TimeManager>.instance.today` (`GameDate` struct) | CONFIRMED `TimeManager.cs:19` |
| Pause/speed | `ManagerBehaviour<SpeedControls>.instance.level` / `.isPaused`; `Time.timeScale` | CONFIRMED `SpeedControls.cs:34,43,98` |
| Day/month hooks | `TimeManager.onDayStart/onDayEnd/onWeek*/onMonthStart/onMonthEnd/onYear*` | CONFIRMED `TimeManager.cs:37-51` |

Calendar: **30-day months, 12 months, 360-day years, 7-day weeks.** Year numbering starts at 1. CONFIRMED.

---

## 1. Companies / actors

### 1.1 Class hierarchy (CONFIRMED)

```
MonoBehaviour
 └─ Actor (abstract)               Actor.cs:7          IActor
     ├─ Player (abstract)          Player.cs:5         IPlayer  (has hq)
     │   ├─ HumanPlayer            HumanPlayer.cs:7
     │   └─ AiPlayer               AiPlayer.cs:9
     ├─ State                      State.cs:9          (the "government"/market sink, singleton State.instance)
     └─ SettlementBase (abstract)  SettlementBase.cs:8 (towns; can grant loans and receive upkeep)
```

There is **no dedicated `Company` class**. A "company" is a `Player` actor (`HumanPlayer` or `AiPlayer`)
plus its `IActorComponent` children: `MoneyAgent`, `LoansAgent`, `CompanySharesAgent` (`AiPlayerCompanySharesAgent` for AI),
`CompanyStats`, `ActorStatisticsAgent`, `BankruptcyAgent`, tech tree, contracts, and so on.
Components are resolved with `actor.Get<T>()` (`Actor.cs:157-174`).

### 1.2 Identity (CONFIRMED unless noted)

| Field | Member | Notes |
|---|---|---|
| Numeric id | `Actor.id` (`Actor.cs:37`) | Allocated by `ActorManager.GetUniqueActorId()` (`ActorManager.cs:24`, `_uniqueActorId` serialized). It is restored on load through `_constructorParameters` (`HumanPlayer.cs:15`, `AiPlayer.cs:67-74`). HIGH CONFIDENCE that it is stable across save/load. |
| Name | `Actor.actorName` (`Actor.cs:49`) | Human: **overwritten on every world-ready** from `GameOptions.playerName` (PlayerPrefs, default `"My Corporation"`) (`HumanPlayer.cs:27`, `GameOptions.cs:65-69`). AI: `"<name> <suffix>"` from `AiPlayerNameList` (`AiPlayerManager.cs:185-191`). |
| Color | `Actor.color` (`UnityEngine.Color`) (`Actor.cs:61`) | Human: the entry of `PlayerColors.colors` at index `GameOptions.playerColorIndex` (`HumanPlayer.cs:28-31`). AI: picked from the remaining palette (`AiPlayerManager.cs:107-128`). |
| Logo | none found | grep for `logo` and `companyName`: no hits. HQ visuals exist (`Headquarters`, `HQVisuals`, `HeadquartersVisualsConfig`) but there is no logo concept. INFERRED. |
| HQ | `Player.hq` (`Building`), `Player.hqBuilt` (`Player.cs:22-41`) | `hq.settlement.settlementName` gives the HQ town (`PlayerInfoViewModel.cs:46-56`). |
| Kind | runtime type of the actor: `HumanPlayer`, `AiPlayer`, `State` or `SettlementBase` | Recommend using `Player.humanPlayer` rather than `activeActor` for "the player". |

**`Player.activeActor` caveat (CONFIRMED):** `activeActor` returns the explicitly selected actor (`_activeActor`) when one is set, and falls back to `humanPlayer` otherwise (`Player.cs:14`).
`ActorInfoViewModel.Select()` calls `Player.SetActiveActor(actor)` (`ActorInfoViewModel.cs:30`). That is a UI/debug
path that makes many systems (build costs, money popups, bankruptcy UI, `Headquarters.totalAssets`) act on another
company. An observer must never call it. It should report `Player.humanPlayer` and flag the case where
`activeActor` differs from `humanPlayer`.

### 1.3 Enumerating companies

- `AiPlayerManager.aiPlayers` (`AiPlayerManager.cs:32`) returns a `ReadOnlyList<AiPlayer>` wrapping the private `_aiPlayers` list.
- `ActorManager.actors` (`ActorManager.cs:18`) returns a `ReadOnlyList<IActor>` wrapping the private `_actors` list.

`ReadOnlyList<T>` is a **struct** wrapper (`ReadOnlyList.cs:6`), so it does not allocate. It still wraps the live list,
so the caller must enumerate on the main thread.

- AI count target: min(region count − 1, `GameParametersManager.world.aiCount`) (`AiPlayerManager.cs:30`). CONFIRMED.
- AI removal (bankruptcy or takeover): `AiPlayerManager.RemovePlayer` (`:62`) demolishes everything and removes the player
  from the list and the runner. `ActorManager.DeregisterActor` fires `actorDeregistered` (`ActorManager.cs:310-315`). A removed AI
  disappears from both lists. Snapshot ids and names before they vanish. CONFIRMED.
- `ActorManager.actorRegistered` / `actorDeregistered` events (`ActorManager.cs:20-22`) let an observer track the roster.

---

## 2. Money (cash)

### 2.1 Balances: `MoneyManager` (singleton, `[SavegameManagerObject]`)

- `MoneyManager` keeps a savegame-serialized private `_balances` field (a `Balances` wrapper around a `SavegameDictionary<IMoneyAgent,double>`). The public `balances` property exposes the underlying `Dictionary<IMoneyAgent, double>` (`MoneyManager.cs:78-81`). CONFIRMED.
- Cash is a **`double`** keyed by the actor's `IMoneyAgent` component. CONFIRMED.
- `GetBalance` (`:104`) returns +Infinity when the agent's `infiniteMoney` is true, otherwise the raw balance. CONFIRMED.
- `GetRawBalance` (`:114`) returns the raw stored value. CONFIRMED.
- **Side effect:** both `GetBalance` and `GetRawBalance` call `RegisterAgent` on the agent first (`:106,116`). That method **inserts a
  0 entry** if the agent is unknown, and the entry is persisted in the save. For read-only use, prefer
  a `TryGetValue` lookup on `balances` plus `HasAgent` (`:91`), which has no side effects. CONFIRMED.
- Negative balances are allowed. `MoneyAgent.canGoNegative` (`MoneyAgent.cs:25`) only affects `CanPay`. CONFIRMED.
- Starting cash: `MoneyAgent.startingBalance` (serialized prefab field) is added in `OnLateWorldBecameReady` on a new game
  (`MoneyAgent.cs:~160-166`). The value is UNKNOWN (prefab). `DifficultyParameters.balance` exists, but no code reads
  `difficulty.balance`. INFERRED: it is unused, or applied in prefab/game-data.
- Infinite money: `MoneyAgent.infiniteMoney` is true when the serialized `_infiniteMoney` flag is set, or when
  `ScenarioManager.HasInfiniteMoney` returns true for the agent (it returns `isEditing`)
  (`MoneyAgent.cs:36-45`, `ScenarioManager.cs:325-328`). `difficulty.infiniteMoney` is OR-ed in on a new game (`MoneyAgent.cs:~163`). CONFIRMED.
  The **State** actor probably has infinite money. INFERRED (prefab).

### 2.2 The only money-moving path: `MoneyManager.EasyMoneySend` (CONFIRMED `MoneyManager.cs:126-148`)

Behaviour, given a receiver, a sender, an amount and a bill category:
- One `MoneyBill` is created for the transfer, with the **receiver passed first and the sender second** (see the naming trap below).
- If both parties keep historical data, the sender gets its own clone of that bill; otherwise both parties share the same bill object.
- The receiver side is processed unless the receiver has infinite money and the category's `infiniteMoneyHandleAnyway` is off:
  the receiver handles the bill (`HandleBill`) and its balance increases by the amount.
- The sender side follows the same rule: unless the sender has infinite money and `infiniteMoneyHandleAnyway` is off,
  the sender handles its bill and its balance decreases by the amount.

**Naming trap (CONFIRMED):** `MoneyBill.Create` declares its first two parameters as originator then recipient (`MoneyBill.cs:26`),
but `EasyMoneySend` passes the receiver first and the sender second. So in a stored `MoneyBill`:
- `bill.originator` = the party that **received** the money (it issued the invoice),
- `bill.recipient` = the party that **paid** (it received the invoice).

The rest of the code is consistent with this. `GetExpenses` counts bills whose `recipient` is the agent itself (`MoneyAgent.cs:219`).
`GetIncome` counts bills whose `recipient` is someone else (`:235`). `BudgetCatVMEntry.IsRelevant` uses the same convention (`BudgetCatVMEntry.cs:~71`).

`MoneyManager.Transfer` (`:96`) moves balances **without** a bill. No call sites were found. `SetRawBalance` is used only for the
starting balance. CONFIRMED (grep).

### 2.3 Bankruptcy (CONFIRMED `BankruptcyAgent.cs`)
- There is **no persistent "bankrupt" flag**. `BankruptcyAgent.OnMonthEnd` (`:40-51`) checks whether the balance (`GetBalance`) is below 0. If so, it sets a
  Steam stat, dispatches a `BankruptEvent` carrying the actor through `EventDispatcher`, and calls `StartBankruptcyAuction()`.
- It is subscribed only when not in a tutorial, the scenario allows bankruptcy, and the agent does not have infinite money (`:53-65`).
- For **AI**, `StartBankruptcyAuction` removes the HQ, calls `AiPlayerManager.RemovePlayer` (which demolishes everything), and
  enqueues an assets auction of its buildings and full-permit regions (`:10-38`).
- For the **human**, `BankruptcyUI` listens for a `BankruptEvent` whose actor is `Player.activeActor` and opens the fullscreen
  "Bankruptcy" panel (`BankruptcyUI.cs:70-79`). The player can take one `LoanType.BANKRUPTCY` loan (`:48-59`) or give up.
  `gameOver` is true when no loan can be taken (`canTakeLoan` false) or the player gave up (`_gaveUp`) (`:20-30`). It is UI state only and is not serialized.
- Derived observer state: "at risk" = balance below 0 (the check runs at the next month end). "Bankruptcy loan active" =
  any loan whose `type` is `LoanType.BANKRUPTCY`. INFERRED as a useful proxy.
- `ActorStatisticsAgent.cashflow` (`ActorCashflow`: `BLEEDING, LOSING, GAINING, PROSPERING`, `ActorCashflow.cs:3`) gives a
  coarse health label, recomputed at each month start (see §5.3).

---

## 3. Ledger / financial history

### 3.1 Data model (CONFIRMED)

- `MoneyBill` (`MoneyBill.cs`): `amount` (double), `category` (`MoneyBillCategory`), `recipient` (`IMoneyAgent`, the payer),
  `originator` (`IMoneyAgent`, the payee), `date` (`GameDate`). Saved via custom binary serialization: category is saved **by asset name**,
  actors by GUID (`:71-93`).
- `MoneyBillCategory` (`MoneyBillCategory.cs`) is a `ScriptableObject` game-data asset (game-data type "moneyBillCategories") with
  `categoryName`, `svgIcon`, `infiniteMoneyHandleAnyway`. **It is not an enum.** The set of categories is game data,
  enumerable at runtime via `GameData.instance.GetAssetsRO` for the `MoneyBillCategory` type (used at `MoneyAgent.cs:~132`). CONFIRMED.
- `MoneyOverviewCategory` (`MoneyOverviewCategory.cs`) is a `ScriptableObject` that groups bill categories for the Money panel:
  `displayName`, `type` (ONE_TIME or REOCCURRING), `uiOrder`, `billCategories` (a list of `MoneyBillCategory`). CONFIRMED.
- `TotalBudgetEntry` (`TotalBudgetEntry.cs`) is another grouping for the Budget panel: `entryName, category, isExpense, orderIndex, parent`. CONFIRMED.

### 3.2 Storage and retention in `MoneyAgent` (CONFIRMED `MoneyAgent.cs`)

- `_bills` (`:32`) is a private dictionary from an integer key to a `MoneyBillTimeTree`. The key (`GetMoneyBillTileTreeKey`, `:148`)
  is a combined hash of the category's instance id, the payer (`recipient`) id and the payee (`originator`) id.
- `RegisterBill` (`:106`) computes that key, re-dates the bill to the **1st day of its month** (`:109`), and adds it to the tree for
  that key, creating the tree on first use.
- `MoneyBillTimeTree` is a `TimeTree<MoneyBill>` whose aggregator adds each new entry's amount to the existing entry for the same date (`MoneyBillTimeTree.cs`).
  So each (category, payer, payee) pair keeps **one aggregated `MoneyBill` per month**. The first bill object of the month
  is mutated in place, and the per-transaction detail is lost. CONFIRMED (`TimeTree.cs:141-166` aggregator path).
- **Only monthly resolution exists.** There is no daily ledger. CONFIRMED.
- Bills are kept only if `keepsHistoricalData` (serialized `_keepsHistoricalData`, `:23,64`). The value per actor is UNKNOWN (prefab).
  HIGH CONFIDENCE that human and AI players keep history: `ActorStatisticsAgent.ComputeCashFlow` uses `GetIncome/GetExpenses` for
  every player to drive the AI "cashflow" label shown in `PlayerInfoViewModel`, and a PRIVATE-build switcher shows the Money panel for AI.
- **Retention:** `historicalDataRange` defaults to 3 (years, code default, prefab may override; `:27`). `ClearOldData()` runs on
  `TimeManager.onYearEnd` (`:66-74,168`) and removes nodes dated on or before today minus 3 years (a `GamePeriod` of 3 years, 0 months, 0 days). At year end, `today` is still
  `30/12/Y`, so it removes everything up to and including year **Y-3**. **Kept: years Y-2, Y-1, Y**, which grows to almost 4 years
  just before the next year end. CONFIRMED (date arithmetic checked in `GamePeriod.cs:59-62`, `GameDate.AddDays/AddMonths`).
- Bills to or from an actor are dropped when that actor is deregistered (`OnAnyActorDeregistered`, `:~130-140`). So history with a
  bankrupt or taken-over AI **disappears** from the other companies' ledgers. CONFIRMED.
- Other ledgers that are not bills: `State._sales/_salesFigures` (`ProductInfoCollection`), `Shop._sales/_salesFigures/_overallSales`,
  `ProductionStatsTracker` (`TimeTree<float>` per product cost), `BuildingAnalysis.LogAnalysisData` (per-building monthly
  upkeep/uptime/dispatch). These belong to the products/buildings topic and are mentioned here only for cross-reference.

### 3.3 Query API (CONFIRMED `MoneyAgent.cs:198-254`, `TimeTree.cs:192-249`)

- `GetBills`: takes a from/to `GameDate` range, an optional `MoneyAgentGetBillsOptions` (default `SORTED`) and an optional
  pre-allocated list. Returns a list of `MoneyBill`.
- `GetExpenses`: takes a from/to range and an optional category (none = all categories). Returns the total (double) of bills the agent paid.
- `GetIncome`: same inputs. Returns the total of bills the agent did not pay (it was the payee).
- `GetProfit`: takes a from/to range. Returns income − expenses over all categories.

**Range semantics trap (CONFIRMED `TimeTree.GetFromToIndices`, `:228-249`):** nodes are keyed by the 1st of the month.
`fromIndex` is the first node on or after `from`, and `toIndex` is the last node on or before `to`. In effect the query returns **the months whose day-1 key
lies in [from, to]**:
- A `from` of (Y,M,15) **excludes** month M. A `to` of (Y,M,2) **includes** the whole month M.
- Use month-aligned queries: from (Y,M,1) to (Y,M,30) for one month, as the game itself does (`MoneyOverviewUI.cs:137-148`).

How the game's own UI computes figures (good reference implementations):
- Money panel (`MoneyOverviewUI.Update`, `MoneyOverviewUI.cs:130-158`, every 0.1 s unscaled while active):
  - `balance`: `GetBalance` for the agent.
  - `monthlyFlow`: `GetProfit` from (Y,M,1) to (Y,M,30) (current month to date).
  - `lastMonthMonthlyFlow`: `GetProfit` from the 1st of the previous month to the day before (Y,M,1).
  - for each `MoneyOverviewCategory` (ONE_TIME / REOCCURRING) and each of its bill categories: income − expenses for the current and last month.
  - `estimatedSales` = Σ over all settlement shops of price × min(delivered by the actor, demand) (`:190-206`).
  - The panel caches these in **public fields** (`balance`, `monthlyFlow`, `lastMonthMonthlyFlow`, `oneTimeCategories[*].currentMonth/lastMonth`,
    `reoccurringTotal`, ...). Only for the human player, and only while the component's `Update` runs. INFERRED: it may be stale if the UI object is inactive.
- Budget panel (`BudgetCatVMEntry.SetDataRange`, `BudgetCatVMEntry.cs:~39-65`): per category, `GetExpenses` or `GetIncome` plus a
  series of `Vector2` points (day count, absolute amount). Refreshed on `onDayEnd` (`BudgetViewModelUpdater.cs:~25-50`).
- The cashflow label uses only REOCCURRING categories for the previous month (§5.3).

### 3.4 Revenue and expense categories (who calls `EasyMoneySend`)

Category **identities are assets**. The table lists the code-side field that holds each one and the flow (CONFIRMED by grep of all
`EasyMoneySend` call sites). Display names and grouping come from the `MoneyOverviewCategory` / `TotalBudgetEntry` assets (UNKNOWN values).

| Field (holder) | Flow (payer → payee) | Call site |
|---|---|---|
| `MoneyManager.productTradeCategory` | State → company (shop sale: quantity × `GetPrice`) | `Shop.cs:463` |
| same | State → **activeActor only** (product delivered into a State trading building) | `StateTradingHandler.cs:~22-26` (AI selling to State gets no money here; CONFIRMED quirk) |
| `ProductSellerTransportRequestPaymentHandler.productBillCategory` | buyer → seller building owner (inter-company / B2B sale price) | `ProductSellerTransportRequestPaymentHandler.cs:29` |
| `ProductSellerTransportRequestPaymentHandler.dispatchBillCategory` | payer → State (dispatch fee for that sale) | `:28` |
| `DefaultTransportRequestPaymentHandlerBehaviour.billCategory` | building owner (or the info-provider payer) → State, per **vehicle dispatch** | `DefaultTransportRequestPaymentHandlerBehaviour.cs:22-28` |
| `Wholesaler.buyBillCategory` | wholesaler owner → deliverer | `Wholesaler.cs:39` |
| `MoneyManager.upkeepCategory` | building owner → its settlement (or State), **monthly** | `Upkeep.cs:162-176` |
| `MoneyManager.buildingConstructionCategory` | activeActor → State (buildings, roads/rails networks, overpasses, resource nodes, terraforming) | `BuildBuildingMode.cs:189`, `BuildModeConnectivityNetwork.cs:502`, `BuildModeOverPasses.cs:81`, `BuildResourceNodeBuildMode.cs:231`, `TerraformingEditMode.cs:275`, `Demolish.cs:39` (negative refund) |
| `MoneyManager.infrastructureConstructionCategory` | activeActor → State | `BuildMode.cs:22`, `BuildModeConnectivityWormholes.cs:182` |
| `MoneyManager.buildingRefundCategory` | State → demolisher / undo | `Demolish.cs:35`, `BuildModeUndo.cs:51` |
| `TechTreeManagerConfig.researchCostsBillCategory` | company → State, **daily** while researching | `ResearchExpensesHandler.cs:13-25` via `TechTreeAgentResearchState.cs:172` (onDayEnd) |
| `LoansAgent.starterLoanBillCategory` / `loansTakenRepayedBillCategory` | lender → company (loan principal received); company → lender (early repayment) | `LoansAgent.cs:46-51`, `Loan.cs:~99-105` |
| `LoansAgent.loanPaymentsBillCategory` | company → lender, **monthly** instalment | `Loan.cs:83-92` (via `LoansAgent.PayForMonth`, onMonthStart) |
| `CompanySharesAgent.bundlePurchaseBillCategory` | buyer → previous bundle owner | `CompanySharesAgent.cs:152,181` |
| `RegionPurchaseAgent.billCategory` | buyer → previous permit owner (region buyout between companies) | `RegionPurchaseAgent.cs:~112` |
| `MoneyBidController.billCategory` | auction winner → auctioneer (permits, bankruptcy assets, PR & marketing) | `MoneyBidController.cs:16` |
| `MoneyManager.contracts` | settlement ↔ company (delivery contract payout, advancement contract) | `DeliveryContract.cs:165`, `AdvancementContract.cs:51` |
| `Headquarters.changeVisualsBillCategory` | activeActor → State (HQ cosmetic change) | `Headquarters.cs:127` |
| `WorldEventAgent.moneyTransfersBillCategory` | State → activeActor (event **grant**) | `WorldEventAgent.cs:346-349` |
| (fines) | Implemented as a `LoanType.FINE` loan from State, paid monthly | `WorldEventAgent.cs:341-344` |
| `MoneyManager.cheatCategory` | State → human (console cheat) | `PAConsole.cs:222` |

Declared on `MoneyManager` but **with no call sites in code** (HIGH CONFIDENCE legacy or unused): `buildingBuyoutCategory`,
`rentCategory`, `vehicleCategory`, `vehicleUpkeepCategory`, `transportRouteCategory`, `permitPurchaseCategory`,
`terraformingCategory` (`MoneyManager.cs:35-76`).

**No salaries or wages exist in RoI** (grep `salar|wage|payroll`: no hits). Labour cost is folded into building upkeep. CONFIRMED (absence).

Upkeep mechanics (CONFIRMED `Upkeep.cs`):
- The monthly rate is max(baseCost × buildingCostPercentage (0.25) [+ additional delegates] × modifiers, baseCost × 0.25 × minUpkeep (0.25))
  (`:86-103`). Modifiers = difficulty `upkeep` × actor modifiers × building efficiency (`:178-194`). Defaults come from code; prefabs may override.
- Disabled or not-working buildings pay `GetMinUpkeep()` (`:105-117`).
- It **accrues daily** (each `onDayEnd` adds one thirtieth of the active monthly rate to the accrued amount) and is **charged once at `onMonthEnd`** to the settlement or State (`:143-176`).
- The accrued, uncharged amount is the private `upkeep` field (float, savegame field). Use `totalActiveMonthlyUpkeep` for a forecast.

AI construction: `ActorBuildingQueue` (the AI build path) contains no money calls (grep). HIGH CONFIDENCE that **AI pays no
construction cost through bills**. AI still pays upkeep, dispatch, research (owner-based paths) and share purchases.

---

## 4. Loans (CONFIRMED `LoansAgent.cs`, `Loan.cs`, `LoanInfo.cs`, `LoanType.cs`)

- Access: `actor.loans` → `ILoansAgent` (`Actor.cs:87`). `loans.loans` is the live `List<Loan>`. `maxLoans` returns the serialized `_maxLoans` (default 3, prefab) (`LoansAgent.cs:26,33-35`).
- `LoanType { STARTER, SETTLEMENT, BANKRUPTCY, FINE }` (`LoanType.cs:3`).
- `LoanInfo` (ScriptableObject): `type, title, icon, amount, apr, duration (months), firstPaymentAfter (grace months), actorReceivesMoney` (`LoanInfo.cs:9-23`).
- `Loan` public read members: `grantingActor` (State or a settlement), `type`, `title`, `amount` (principal, float), `apr`,
  `amountWithApr` (= amount × (1 + apr)), `duration`, `remainingPayments` (= duration − payments made so far, `_paymentsAmount`),
  `amountToPay` (= remainingPayments / duration × amount, the early-repay amount, **principal only**), `eventData` (for fines).
  The grace counter `freeMonthsLeft` is **private** (`Loan.cs:31`). CONFIRMED.
- Monthly instalment: modifier × amount × (1 + apr) / duration. The modifier is the paying actor's loan-payment modifier for the
  lending settlement (`modifiers.GetLoanPaymentModifier`) for settlement loans, otherwise 1 (`Loan.cs:107-121`). **Flat interest, not amortized or compounding.** CONFIRMED.
- Schedule: `LoansAgent` subscribes to `TimeManager.onMonthStart` → `PayForMonth()` (`LoansAgent.cs:109,125-144`). Each loan
  either decrements its grace months or pays one instalment. It is removed once `_paymentsAmount` reaches `duration`. Payments are dated **day 1 of the new month**.
- Starter loan: on a new game, if `difficulty.loan` is positive, a loan from State is added using the `starterLoan` info, with principal
  `difficulty.loan` × 1,000,000 and the starter loan's own APR and duration (`LoansAgent.cs:118-122`).
- Sources: settlement loans from the loans panel (`LoansPanelUIViewModel.cs:124`), bankruptcy loan (`BankruptcyUI.cs:52`), fines
  (`WorldEventAgent.cs:343`). Only one loan per settlement (`CanTakeLoanFromSettlement`, `LoansAgent.cs:94-104`).
- Whether AI players have a `LoansAgent` is UNKNOWN (prefab). No AI code calls `AddLoan` (grep). HIGH CONFIDENCE that AI takes no
  loans beyond a possible starter loan.
- Event: `ILoansAgent.loanRepaid` (`Action<Loan>`), fired only on **early** repay (`LoansAgent.cs:64-79`), not on natural completion.

---

## 5. Competitors (AI), shares, company value

### 5.1 AI player (CONFIRMED `AiPlayer.cs`, `AiPlayerManager.cs`, `AiPlayerRunner.cs`)

Readable state on `AiPlayer`:
- `personality` (`AiPlayerPersonality` asset: `freeUnlocks`, `demandFulfilledThreshold`, `auctions` evaluator, `payOutAdvancementChance`,
  `advancementContractsVirtualRemainingDays`, `hatedBuildings`) (`AiPlayerPersonality.cs`).
- `brain` (`AiPlayerBrain` asset), `difficultyPreset`.
- `ownedRegions` (`ReadOnlyList<Region>`, `:38`). `hasInitiative` (the inverse of the private `_noInitiative`, `:40-50`).
- `GetState()` / `GetState<T>()` → `AiPlayerBrainState`. With the behaviour-tree brain this is `BehaviourTreeAiPlayerBrainState`
  (`BehaviourTreeAiPlayerBrainState.cs`): `productGoals`, `productGoalsOnHold` (`ReadOnlyList<AiProductGoal>`, each with `product`,
  `destination` (a `BuildingLogistics`), and abstract `IsSatisfied/GetDailyNeed/GetUrgency(player)`). The BehaviorDesigner `BehaviorTree`
  component is on the AI GameObject (`tree`).
- Components: `buildingQueue` (`AiPlayerBuildingQueue`), `zones` (`AiPlayerZoneManager`), `repurposing`, `ExpansionAgent`
  (cooldowns, `expansionChance`), `AiPlayerRegionPurchaseAgent`, `AutoBiddingAuctionsAgent`, `AiPlayerCompanySharesAgent`.
- Buildings: `actor.buildings` (`IActorBuildingCollection`: `count`, `recipeUsers`, `Filter/Count(prefab|tag)`, enumerator). It has a
  `ReaderWriterLockSlim` with public `EnterReadLock/ExitReadLock` (`IActorBuildingCollection.cs:20-22`, `ActorBuildingCollection.cs:12,48-56`).
- Cash, ledger, loans, shares, stats: same components as the human (§2-§4, §5.2-§5.3).

Behaviour cadence:
- `AiPlayerManager.OnDayStart` → `_runner.DailyTick()` → the scheduling strategy picks **one** acting player per day →
  `TakeTurn()` (`AiPlayerManager.cs:224-227`, `AiPlayerRunner.cs:35-41`).
- `AiPlayerManager.Update` → `_runner.Update()` → `player.Tick()` for every AI **every frame** while `ScenarioManager.CanTickAi()` (`AiPlayerManager.cs:229-235`, `AiPlayerRunner.cs:43-50`).
- Share purchases: `AiPlayerCompanySharesAgent` uses `onDayEnd`, `_cooldownInDays` (initial `purchaseInitialCooldownInMonths×30`, then
  `purchaseCooldownInMonths×30`). It buys back its own bundles with chance `purchaseOwnBundlesChance`, otherwise picks among competitors' bundles
  priced at most `bundlePriceThreshold` × own bundle price (`AiPlayerCompanySharesAgent.cs:21-166`). Uses `UnityEngine.Random`.
- There is no explicit "behaviour state enum" (aggressive, idle, ...) beyond `AiPlayerTurnResult { IDLE, THINKING, DONE }`
  (the return of `Tick`, not stored) and the goal lists. CONFIRMED.

### 5.2 Shares (CONFIRMED `CompanySharesAgent.cs`, `CompanyShareBundle.cs`)

- Every player has `bundleCount` (10) bundles, each of `size` 1 / `bundleCount` = 0.1. They are created on a new game, or when a
  pre-v112 save has none (`CompanySharesAgent.cs:11,275-290`).
- `CompanyShareBundle` holds `size` (float), `company` and `owner` (both `IActor`) (`CompanyShareBundle.cs:6-29`).
- Ownership questions:
  - Bundles of company X: `X.Get<CompanySharesAgent>().bundles`.
  - How many of X's bundles competitors own: `CountBundlesOwnedByCompetition()` (`:62-73`).
  - Bundles that X owns in other companies: `GetOwnedBundles(...)` / `GetOwnedBundlesExcludingMyCompany(...)` (`:88-127`). These use `ListPool`, so return the list.
  - `canSellBundles`: X still owns more than `sellMinLeftBundleCount` (3) of its own bundles (`:26-40`).
- Prices:
  - Purchase price = min(round(size × CompanyValue), 999,000,000) (`:52-55`).
  - Sell price (selling your own bundle) = min(round(size × RegionsValue), 999,000,000) (`:57-60`).
- **Hostile takeover:** when all 10 bundles are owned by one competitor, `TransferCompanyOwnership` runs (`:206-273`). It demolishes the HQ,
  transfers regions and permits, gives the tech unlocks, re-points bundles the victim owned, dispatches a `HostileTakeoverEvent` carrying the original and the new owner,
  and calls the player's `Kill()` (deregisters and destroys the actor). If the new owner is the human, `OpenSellCompanyBuildingsRequestEvent` fires.
- Events: `CompanySharePurchasedEvent` carrying the buyer (`CompanySharePurchasedEvent.cs`), `HostileTakeoverEvent`, and the world event
  `shareBundleAcquiredEvent` notification for the human.

### 5.3 Company value, score, statistics

- `CompanyStats` (`CompanyStats.cs`):
  - `ComputeRegionValue(region)` = permit cost of the region + Σ `paidToBuildAmount` of the actor's buildings in that region (`:7-20`).
  - `ComputeRegionsValue()` = Σ over regions whose permit owner (`PermitManager.GetPermitOwner`) is the actor (`:31-42`).
  - `ComputeCompanyValue()` = RegionsValue × max(1, 1 + 0.1 × bundles owned by competition) (`:22-29`). Competitors owning your
    shares **raise** your buy-in price.
  - Cash is not part of company value. CONFIRMED.
- `Headquarters.totalAssets` = Σ `paidToBuildAmount` over **all** buildings whose owner is `Player.activeActor` (`Headquarters.cs:50-62`). This is
  actor-agnostic despite living on an HQ component. Quirk: calling it on an AI HQ still returns the active player's assets. CONFIRMED.
- End-game score: `EndGameManager.GetFinalScore()` evaluates the `scoreFormula` asset with three inputs: assets = balance of the
  active actor + the human HQ's `totalAssets`; difficulty = `difficulty.scoreModifier`; months = today's `CountDays()` / 30 (`EndGameManager.cs:40-49`). The formula is game data (UNKNOWN).
  It throws NRE if the human HQ is not built. `EndGameManager.gameEnded` (`:38`) is serialized.
- Scenario profit objective: total assets = HQ.totalAssets + balance (`ScenarioProfitObjective.cs:45-48`).
- `ActorStatisticsAgent` (`IStatisticsAgent`), recomputed at **onMonthStart** (`ActorStatisticsAgent.cs:73-74`):
  - `cashflow` (`ActorCashflow`) from previous-month REOCCURRING income minus expenses, together with the current balance (`:168-195`):
    net below 0 and balance below 0 → BLEEDING; net below 0 and balance above 0 → LOSING; net above `prosperingThreshold` → PROSPERING; otherwise GAINING.
  - `topProduction`, `topSales` (`List<Product>`, top N over `productStatisticsRange`), `ownedPermits`, `mainTechTree`, `CountBuildingsWithTag(tag)`.
  - This is the data behind the "Players" info panel (`PlayerInfoViewModel.cs`): HQ region, region count, building counts by tag,
    main tech tree, top production and sales, cashflow label, shares. It is the game's intended "competitor intel".

---

## 6. Game time / calendar

### 6.1 `TimeManager` (singleton, `[SavegameManagerObject]`, `TimeManager.cs`) (CONFIRMED)

Members:
- `secondsPerDay` (float, public, `:13`): real (scaled) seconds per game day. Not visible in code (scene/prefab); 8.0 per the errata above.
- `seasonStartMonth` (int, public, `:16`).
- `today` (`GameDate`, public, `:19`): a public field grouped under a "DEBUG" inspector header, but it is the real state.
- `day`, `month`, `year` (int, public, `:21-25`).
- `dateVisual` (string, public, `:27`), e.g. "Jan 05, Y3".
- `_days` (int, private, savegame-serialized, `:30`): absolute day counter, the source of truth.
- `_months` (int, private, savegame-serialized, `:33`).
- `startDate` (`GameDate`, publicly readable, `:35`): set in `Awake`, NOT serialized.
- Events (`TimeManagerCallback`, `:37-51`): `onDayStart`, `onWeekStart`, `onMonthStart`, `onYearStart`, `onDayEnd`, `onWeekEnd`, `onMonthEnd`, `onYearEnd`.
- `GetDayOfWeek()` (`:53`): `_days` mod 7.

- Derivation: day = (`_days` mod 30) + 1, month = (`_months` mod 12) + 1, year = `_months` / 12 + 1, with `_months` = `_days` / 30 (integer division throughout) (`:63-69,133-140`).
- Start: `_months` starts at `seasonStartMonth` when `GameOptions.seasons` is on, otherwise at 0; `_days` starts at `_months` × 30 (`:142-149`). Without seasons the game starts
  1 January, Year 1. `startDate` is recomputed in `Awake` from the *current* settings, not from the save. INFERRED: unreliable for loaded games.
- **Tick:** `OnLateWorldBecameReady` schedules `NewDay` with Unity's `InvokeRepeating`, first after `secondsPerDay` and then every
  `secondsPerDay` (`:151-154`). `InvokeRepeating`
  runs on **scaled time** (Unity behaviour, HIGH CONFIDENCE), so a `Time.timeScale` of 0 stops days. Whether multiple days fire in one frame at
  high speed is UNKNOWN (Unity Invoke internals).
- **Order inside `NewDay()`** (`:63-104`), which matters for snapshots:
  1. `_days` is incremented by 1.
  2. `onDayEnd` → (`onWeekEnd` if `_days` is divisible by 7) → (`onMonthEnd` if the month rolled) → (`onYearEnd` if the year rolled). **`today` still holds the OLD date.**
  3. `day/month/year/today/dateVisual` are updated.
  4. `onDayStart` → `onWeekStart` → `onMonthStart` → `onYearStart` (with the NEW date).
  Each delegate runs in its own try/catch that logs exceptions (`InvokeCallback`, `:106-126`).
- Consequence: bills created in `onMonthEnd` handlers (upkeep, bankruptcy auctions) are dated **day 30 of the closing month**. Loan
  instalments (in `onMonthStart`) are dated **day 1 of the new month**. CONFIRMED (MoneyBill.Create uses `TimeManager.today`, `MoneyBill.cs:36`).

### 6.2 `GameDate` / `GamePeriod` (CONFIRMED)

- `GameDate` is a readonly struct `(Year, Month, Day)`. Constants: 12 months, 7-day weeks, 30-day months, 360-day years, `MIN_DATE` (1,1,1) (`GameDate.cs:9-17`).
  The constructor logs an error (`Debug.LogError`) when day is outside 1..30 or month outside 1..12 (`:40-49`). An observer must not build invalid dates, or it will spam the log.
- `CountDays()` = 360·Y + 30·M + D − 390, so (1,1,1) → 1. Relation: `today.CountDays()` equals `_days` + 1 (`:103-106`).
- `ToUiString()` → `"Jan 05, Y3"`. `ToString()` → `"5/1/3"` (D/M/Y). `ToFullString()` → `"05/January/0003"`.
- `GamePeriod` holds years, months and days, with `CountDays()`. Adding or subtracting a `GamePeriod` to or from a `GameDate` calls `AddDays` with plus or minus the period's day count (`GamePeriod.cs:22-62`).
- `GameDate.RandomInRange` uses `UnityEngine.Random`. Avoid it (RNG side effect).
- `Month` enum is a **flags** enum (Jan=1, Feb=2, Mar=4, ...) used for seasonal masks. It is not the `GameDate.Month` integer (`Month.cs`).

### 6.3 Speed and pause: `SpeedControls` (singleton, serialized) (CONFIRMED `SpeedControls.cs`)

- `level` (`int`, serialized): `-1` = paused (`PAUSE_SPEED_LEVEL`), otherwise an index into `speedLevels[]` (float multipliers; values UNKNOWN, prefab).
  `isPaused` is true when `level` is -1. `levelBeforePause`. `levelCount`.
- `UpdateTimeScale()` sets `Time.timeScale` to 0 when `level` is -1, otherwise to the `speedLevels` entry at index `level` (`:96-99`).
- Every frame `Update()` lets `IGameSpeedOverride`s (TutorialManager, ScenarioManager, State, FullscreenPanelManager, self) force a level
  (`:142-160`). `OverrideSpeedLevel` freezes when the main menu is shown or a text field has focus (`:192-200`).
- Other code sets `Time.timeScale` to 0 directly: `PauseMenuHelper` (`:33-34`), `MainMenu` (`:168-169`), `EndPopup` (`:38-39`), `WorldLoadingScreen` (`:74`).
  So "effectively paused" = `Time.timeScale` is 0, which can be true while `SpeedControls.level` is not -1. Report both.
- Event: `SpeedChangeEvent` (old speed, new speed, user-changed flag) through `EventDispatcher`, plus `SpeedControls.onLevelChangeEvent`.
- On a new game, the game starts **paused** (`OnWorldBecameReady` → `SetPause`, `:207-218`). Speed keys are ignored until the human HQ is built (`:161-164`).

### 6.4 Economy cadence (who runs when). Subscribers, CONFIRMED by grep

| Hook | Finance-relevant work |
|---|---|
| per frame (`Update`) | AI `Tick()` for all AI; `EndGameManager` condition checks (every `updateInterval` s, unscaled); Money panel refresh (0.1 s unscaled) |
| `InvokeRepeating` per vehicle dispatch | dispatch fees (`DefaultTransportRequestPaymentHandlerBehaviour.PayVehicleDispatch`), B2B sales |
| `onDayEnd` | upkeep accrual; research daily cost (`TechTreeAgentResearchState`); AI share-purchase cooldown; `ScenarioManager` objective polling; Budget panel refresh; auctions; GlobalMarket; settlements; contracts |
| `onDayStart` | AI turn (one AI per day); `WorldEventManager`; `ContractSpawner`; `Settlement` |
| `onMonthEnd` | **upkeep charged**; **bankruptcy check**; dispatch analytics reset; `State` sales pruning; `SettlementContractManager.InitializeContracts`; `WorldEventManager` |
| `onMonthStart` | **loan instalments**; `ActorStatisticsAgent` cashflow and top-products recompute; `ProductionStatsTracker` |
| `onYearEnd` | **`MoneyAgent.ClearOldData()`** (ledger pruning); `RecipeUser`/`Shop` yearly resets |

The order between subscribers of the same event follows subscription order (for example, Upkeep subscribes in `Start`, BankruptcyAgent in
`OnWorldBecameReady`). So whether the month's upkeep is charged before the bankruptcy check on the same `onMonthEnd` is INFERRED, not guaranteed.

### 6.5 Recommended snapshot points for an observer
- **Per day:** detect a change of `TimeManager.today` (or `_days`) by polling on the main thread, or append a handler to `onDayStart`.
  At `onDayStart` every end-of-day effect of the previous day has been applied.
- **Per month:** at `onMonthStart`, or the first poll where today's `Day` is 1, the previous month's ledger (`(Y,M-1,1)`..`(Y,M-1,30)`) is final.
  The `onMonthEnd` charges are dated in the old month. Loan instalments then land in the new month, dated day 1. `ActorStatisticsAgent.cashflow` may
  or may not be refreshed yet, depending on handler order, so recompute it yourself or read it one poll later. INFERRED.
- **Per year:** snapshot the ledger **before** `onYearEnd` pruning if you need history longer than about 3 years. Something like
  `onDayEnd` while today is month 12, day 30 works; `onYearEnd` handlers also run before `today` is updated. Better: persist your own
  monthly aggregates, because the game's retention is about 3 years and counterparties that get deregistered vanish.
- Subscribing to `TimeManager` events mutates the game's delegate lists. That is benign but not strictly "zero-touch". Polling `today` from
  a main-thread hook avoids it. `EventDispatcher.AddReceiver` is keyed by the receiver object and replaces any previous handler for that
  receiver. Adding a receiver *during* a dispatch of the same event type would modify the dictionary under enumeration. INFERRED risk.

---

## 7. Methods and properties that are unsafe or costly for a read-only observer

**Threading (INFERRED, high importance):** all of this is Unity main-thread state, with plain `Dictionary`/`List` and no locks (except
`ActorBuildingCollection`). `ManagerBehaviour<T>.instance` calls `Object.FindObjectOfType` when the cached instance is null
(`ManagerBehaviour.cs:13-38`). That is a Unity API call and is illegal off the main thread. `ListPool<T>` is a static, unsynchronized pool.
`TimeTree.Enumerator` throws `InvalidOperationException` if the tree changes during enumeration (`TimeTree.cs:~31`). **Do every read on the main thread** (for example from a MonoBehaviour `Update` or a TimeManager callback) and copy the values out.

### 7.1 Mutating: never call (CONFIRMED)
- `MoneyManager`: `RegisterAgent`, `Transfer`, `SetRawBalance`, `EasyMoneySend`; also **`GetBalance` and `GetRawBalance`, which can insert an entry** (use `balances.TryGetValue`).
- `MoneyAgent`/`IMoneyAgent`: `HandleBill`, `ClearOldData`, `OnSavegameSerialize` (rebuilds `_savegameBills`), `AfterSavegameLoad`, the `infiniteMoney` setter, the `startingBalance` setter.
- `LoansAgent`/`ILoansAgent`: `AddLoan`, `RepayLoan`. `Loan`: `PayForMonth`, `Repay`, `apr` setter, `AfterSavegameLoad`.
- `CompanySharesAgent`: `PurchaseBundle`, `SellBundle` (moves money, may trigger a hostile takeover that kills an actor). `CompanyShareBundle.Purchase`, `owner` setter.
- `AiPlayer`: `TakeTurn`, `Tick`, `Initialize`, `SetStartingRegion`, `AddRegion`, `RemoveRegion`, `hasInitiative` setter. `AiPlayerManager`: `SpawnPlayer`, `RemovePlayer`.
- `Player.SetActiveActor` / `ClearActiveActor`, `ActorInfoViewModel.Select()/Delete()`, `PlayerInfoViewModel.ToggleInitiative()`.
- `ActorManager.GetUniqueActorId()` (increments a serialized counter), `RegisterActor`, `DeregisterActor`. `Actor.Kill()`.
- `SpeedControls`: `SetLevel`, `SetPause`, `TogglePause`, `UpdateTimeScale`, `ForceTimeScale` (plays audio, dispatches events, changes `Time.timeScale`).
- `TimeManager`: `OnSavegameDeserialize`, any direct writes to `today/day/month/year`, and calling `NewDay` via reflection.
- `BankruptcyUI`: `Activate`, `TakeLoan`, `GiveUp`, `ReturnToMainMenu`. `MoneyOverviewUI`: `SetPlayer`, `ToggleOverviewPanel`.
- `EventDispatcher.DispatchEvent`, `Clear`.

### 7.2 Read-only but with hidden side effects or cost
- `PermitManager.GetPermitOwner/GetPermitCost/OwnsPermitForRegion/IsPermitTaken` look the region up in `_permits` through `GetSafe`, which **inserts an empty
  dictionary** for unknown regions (`PermitManager.cs:184,207`, `Utils.cs:269-278`). It is not saved (only values are serialized), but it is a mutation.
  `CompanyStats.Compute*`, `CompanySharesAgent.GetCompanyValue/GetRegionsValue/GetBundlePurchasePrice/GetBundleSellPrice`, and
  `CompanyShareBundle.GetPrice` all go through this. Cost is O(regions × company buildings) per call. HIGH CONFIDENCE it is harmless on the main thread.
- `Headquarters.totalAssets`: O(all buildings in the world). Uses `activeActor`.
- `EndGameManager.GetFinalScore`: `Formula.Evaluate` allocates an arguments dictionary (`Formula.cs:32-36`). NRE if the human HQ is missing.
- `MoneyAgent.GetBills/GetIncome/GetExpenses/GetProfit`: O(#(category,counterparty) trees × months). They rent from `ListPool` (return lists you get from
  `GetBills`; the totals helpers return theirs). `SORTED` sorts the returned list in place. A full Money-panel recomputation is about
  (#categories × 4) calls. Fine monthly, wasteful per frame.
- `MoneyOverviewUI.ComputeEstimatedSales`: iterates every shop × product in every settlement.
- `Actor.Get<T>()`: lazily fills `_componentMap` via `GetComponentInChildren` (a Unity API call; benign cache write).
- `AiPlayer.TryGetDifficultyParametersOverride`: lazily builds and caches parameters, and may `Debug.LogException`.
- `AiPlayer.GetBehaviourTreeState`: BehaviorDesigner `GetVariable` lookup. `AiProductGoal.IsSatisfied/GetDailyNeed/GetUrgency`: side effects UNKNOWN (abstract, implementation not reviewed).
- `PlayerInfoViewModel.cashflow` in PRIVATE builds calls `GetIncome`. `companySharesViewModel` lazily allocates.
- `GameDate` constructor: logs an error on invalid day or month. `GameDate.RandomInRange`: consumes RNG.
- `ActorManager.actors`, `AiPlayerManager.aiPlayers`, `AiPlayer.ownedRegions`, `CompanySharesAgent.bundles`: struct wrappers over **live** lists.
  `ActorStatisticsAgent.topSales/topProduction/ownedPermits` and `LoansAgent.loans` return the **live mutable** `List<>`. Copy them and never modify.

### 7.3 Safe, cheap reads (CONFIRMED pure getters)
`Player.humanPlayer`, `Player.activeActor`, `Player.hq/hqBuilt`, `Actor.id/actorName/color`, `MoneyManager.HasAgent`, `MoneyManager.balances`
(`TryGetValue`), `MoneyAgent.keepsHistoricalData/canGoNegative/historicalDataRange/startingBalance` (getter), `infiniteMoney` getter (calls
`ScenarioManager.HasInfiniteMoney` → `isEditing`), `Loan.*` getters (except `GetMonthlyPaymentAmount`, which is pure but reads modifiers),
`LoansAgent.maxLoans/CanTakeNewLoan/CanRepay/CanTakeLoanFromSettlement`, `CompanyShareBundle.size/company/owner`,
`CompanySharesAgent.bundleCount/canSellBundles/CountBundlesOwnedByCompetition`, `ActorStatisticsAgent.cashflow/mainTechTree`,
`TimeManager.today/day/month/year/dateVisual/secondsPerDay/GetDayOfWeek`, `SpeedControls.level/isPaused/levelCount/speedLevels`,
`Time.timeScale`, `EndGameManager.gameEnded`, `ResearchExpensesHandler.currentResearchCost`, `Upkeep.totalActiveMonthlyUpkeep` (light math).

---

## 8. Open questions (need live inspection or asset extraction)
1. Runtime values: `TimeManager.secondsPerDay`, `SpeedControls.speedLevels`, `MoneyAgent.historicalDataRange` / `keepsHistoricalData` /
   `startingBalance` / `_infiniteMoney` per actor prefab (Human, each AI prefab, State, settlements). UNKNOWN.
2. Names and grouping of `MoneyBillCategory`, `MoneyOverviewCategory`, `TotalBudgetEntry` assets (the human-readable category list). UNKNOWN. Enumerate them at runtime via `GameData.instance.GetAssetsRO` for each asset type.
3. Whether AI prefabs carry `LoansAgent`, `BankruptcyAgent`, `ActorStatisticsAgent` (code paths suggest yes for the last two). UNKNOWN.
4. Whether `InvokeRepeating` fires several `NewDay` per frame at the top speed. That affects per-day snapshot granularity if the observer polls once per frame. UNKNOWN.
5. `scoreFormula` / `EndGameCondition` asset contents. UNKNOWN.
