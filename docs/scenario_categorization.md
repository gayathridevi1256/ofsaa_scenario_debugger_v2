# Scenario XML Files Categorization

> **Source:** `sceanrio_xml/` directory  
> **Total Files:** 173  
> **Classification Basis:** `@MINER@` schema object usage in SQL query content (`SQL_TX` columns)

---

## Category 1: Files with `@MINER@.F_` Functions (12 files)

These scenarios call stored functions in the `@MINER@` schema (e.g., `@MINER@.F_CheckMISeqAC`, `@MINER@.F_CheckKiting`, `@MINER@.F_STRUCTURING_CU_R`, etc.).

| # | File |
|---|------|
| 1 | `FR-ChkMISequentialNumber.117350046.xml` |
| 2 | `FR-Kiting.117350005.xml` |
| 3 | `ML-ChkMISequentialNumber.114000065.xml` |
| 4 | `ML-ChkMISequentialNumber.114000071.xml` |
| 5 | `ML-ChkMISequentialNumber.118860034.xml` |
| 6 | `ML-ChkMISequentialNumber.118860035.xml` |
| 7 | `ML-PotStructuringCashAndEquiv.118725006.xml` |
| 8 | `ML-PotStructuringCashAndEquiv.118860031.xml` |
| 9 | `ML-StructuringAvoidReportThreshold.116000046.xml` |
| 10 | `ML-StructuringAvoidReportThreshold.118860028.xml` |
| 11 | `ML-StructuringAvoidReportThreshold.118860029.xml` |
| 12 | `ML-StructuringAvoidReportThreshold.118860030.xml` |

---

## Category 2: Files with `@MINER@.ANOMATMEXCESS_PIPELINE` (1 file)

Uses the Oracle pipelined table function for anomaly detection.

| # | File |
|---|------|
| 1 | `ML-AnomATMBCExcessiveWD.116000065.xml` |

---

## Category 3: Files with `@MINER@` Tables/DSVIEW in SQL (121 files)

These reference `@MINER@.DSVIEW*`, `@MINER@.External_Entity`, `@MINER@.cash_trxn`, `@MINER@.mi_trxn`, `@MINER@.wire_trxn`, etc. in their SQL queries (but NOT functions or the pipeline).

| # | File |
|---|------|
| 1 | `FR-AnomATMBCForeignTrans.117350029.xml` |
| 2 | `FR-AnticipateProfileExpectedActivity.117350032.xml` |
| 3 | `FR-BustOutRevolvingCrUse.118745003.xml` |
| 4 | `FR-ChgOfAddrOrPhChkIss.118745014.xml` |
| 5 | `FR-CIBForeignActivity.117350024.xml` |
| 6 | `FR-CIBHRGActivity.117350027.xml` |
| 7 | `FR-CIBProductUtilization.117350015.xml` |
| 8 | `FR-DepWDSameAmts.117350042.xml` |
| 9 | `FR-DepWDSameAmts.117350045.xml` |
| 10 | `FR-EmployeeATMOnlineWithdrawal.118745397.xml` |
| 11 | `FR-EmployeeFOTUnrelatedAccounts.118745393.xml` |
| 12 | `FR-EmpTransSameAmts.118745002.xml` |
| 13 | `FR-EmpTransSameAmts.118745006.xml` |
| 14 | `FR-FTNAcCuInternal.117350022.xml` |
| 15 | `FR-FTNAcCuInternal.117350023.xml` |
| 16 | `FR-FTNCuEnExternal.117350039.xml` |
| 17 | `FR-HRTransFocalHRE.117350031.xml` |
| 18 | `FR-HRTransFocalHRE.117350033.xml` |
| 19 | `FR-HRTransHRCounterParty.117350037.xml` |
| 20 | `FR-HRTransHRCounterParty.117350047.xml` |
| 21 | `FR-HRTransHRGeography.117350019.xml` |
| 22 | `FR-HRTransHRGeography.117350025.xml` |
| 23 | `FR-NetworksOfAcEn.117350036.xml` |
| 24 | `FR-PotentialFraudOnSrAcct.118745011.xml` |
| 25 | `FR-RapidMvmtFundsAllActivity.117350012.xml` |
| 26 | `IML-DataManipulation-dINST.114000059.xml` |
| 27 | `IML-FrqntChngsToInstructions-dINST.114000022.xml` |
| 28 | `IML-HiddenRelationships-dINST.116200006.xml` |
| 29 | `IML-HighRiskEFT-dINST.114000027.xml` |
| 30 | `IML-ICIBInactiveToActive-dINST.116100001.xml` |
| 31 | `IML-ICIBTradeTransActivity-dINST.114000042.xml` |
| 32 | `IML-MvmtFundsWoTrade-dINST.114000020.xml` |
| 33 | `IML-TradesNearMaturityorExpiration-dINST.115200002.xml` |
| 34 | `ML-AdWithMultEn-fAD.115000009.xml` |
| 35 | `ML-AnomATMBCExcessiveWD.116000070.xml` |
| 36 | `ML-AnomATMBCForeignTrans.116000054.xml` |
| 37 | `ML-AnomATMBCForeignTrans.116000055.xml` |
| 38 | `ML-AnomATMBCStructuredCash.116000056.xml` |
| 39 | `ML-AnomATMBCStructuredCash.116000057.xml` |
| 40 | `ML-AnticipateProfileExpectedActivity.116000107.xml` |
| 41 | `ML-AnticipateProfileIncome.116000090.xml` |
| 42 | `ML-AnticipateProfileSOF.116000059.xml` |
| 43 | `ML-CashTransPossibleCTR.116000037.xml` |
| 44 | `ML-CashTransPossibleCTR.116000049.xml` |
| 45 | `ML-CashTransPossibleCTR.116000073.xml` |
| 46 | `ML-CashTransSignificantCash.116000031.xml` |
| 47 | `ML-ChkMIRecurringReBeKnown.116000050.xml` |
| 48 | `ML-ChkMIRecurringReBeKnown.116000074.xml` |
| 49 | `ML-ChkMIRecurringReBeUnknown-dCWS.114000070.xml` |
| 50 | `ML-CIBForeignActivity.116000053.xml` |
| 51 | `ML-CIBForeignActivity.116000068.xml` |
| 52 | `ML-CIBHRGActivity.116000087.xml` |
| 53 | `ML-CIBHRGActivity.116000089.xml` |
| 54 | `ML-CIBPreviousAverageActivity.116000083.xml` |
| 55 | `ML-CIBPreviousAverageActivity.116000084.xml` |
| 56 | `ML-CIBPreviousAverageActivity.118860023.xml` |
| 57 | `ML-CIBPreviousPeakActivity.116000077.xml` |
| 58 | `ML-CIBPreviousPeakActivity.116000081.xml` |
| 59 | `ML-CIBPreviousPeakActivity.118860024.xml` |
| 60 | `ML-CIBProductUtilization.116000069.xml` |
| 61 | `ML-CIBProductUtilization.116000071.xml` |
| 62 | `ML-DepWDSameAmts.115000003.xml` |
| 63 | `ML-DepWDSameAmts.115000007.xml` |
| 64 | `ML-DepWDSameAmts.118860013.xml` |
| 65 | `ML-DepWDSameAmts.118860020.xml` |
| 66 | `ML-DPGProductUtilization.115690003.xml` |
| 67 | `ML-DPGTotalActivity.114697019.xml` |
| 68 | `ML-DPGTotalActivity.115690004.xml` |
| 69 | `ML-EarlyPOCreditProducts.115400010.xml` |
| 70 | `ML-EarlyPOCreditProducts.115400011.xml` |
| 71 | `ML-EarlyRemoval-dNSR.114690106.xml` |
| 72 | `ML-EnWithMultAd-fEN.115000005.xml` |
| 73 | `ML-EnWithMultId-fEN.115000004.xml` |
| 74 | `ML-EnWithMultNames-fEN.115000008.xml` |
| 75 | `ML-EscalationInactiveAC.116000082.xml` |
| 76 | `ML-ExtMatchedNames.114590002.xml` |
| 77 | `ML-ExtMatchedNames.114590003.xml` |
| 78 | `ML-FTNAcCuInternal.114000046.xml` |
| 79 | `ML-FTNAcCuInternal.114000056.xml` |
| 80 | `ML-FTNClientBanks-dCWS.114000031.xml` |
| 81 | `ML-FTNCuEnExternal.114000077.xml` |
| 82 | `ML-FTNCuEnExternal.114000078.xml` |
| 83 | `ML-FTNRecurringOrBe.114000082.xml` |
| 84 | `ML-HRTransFocalHRE.114000053.xml` |
| 85 | `ML-HRTransFocalHRE.114000060.xml` |
| 86 | `ML-HRTransFocalHRE.114000073.xml` |
| 87 | `ML-HRTransHRCounterParty.114000074.xml` |
| 88 | `ML-HRTransHRCounterParty.114000076.xml` |
| 89 | `ML-HRTransHRCounterParty.114000081.xml` |
| 90 | `ML-HRTransHRCounterParty.114000083.xml` |
| 91 | `ML-HRTransHRCounterParty.114000084.xml` |
| 92 | `ML-HRTransHRGeography.115000049.xml` |
| 93 | `ML-HRTransHRGeography.115000052.xml` |
| 94 | `ML-HRTransHRGeography.118860022.xml` |
| 95 | `ML-HubAndSpoke.118860005.xml` |
| 96 | `ML-HubAndSpoke.118860014.xml` |
| 97 | `ML-LargeReportableTrans.115400007.xml` |
| 98 | `ML-LargeReportableTrans.116000060.xml` |
| 99 | `ML-LargeReportableTrans.116000099.xml` |
| 100 | `ML-LgDeprecAcctValue.115200003.xml` |
| 101 | `ML-NetworksOfAcEn.114697025.xml` |
| 102 | `ML-OrgBenediffInstnCountry.118860017.xml` |
| 103 | `ML-PolicieswithRefunds-dNSR.114690107.xml` |
| 104 | `ML-RapidMvmtFundsAllActivity.116000079.xml` |
| 105 | `ML-RapidMvmtFundsAllActivity.116000080.xml` |
| 106 | `ML-RoundAmounts.114590029.xml` |
| 107 | `ML-RoundAmounts.114590030.xml` |
| 108 | `ML-RoundAmounts.114590031.xml` |
| 109 | `ML-RoutingMultiLocations.118860012.xml` |
| 110 | `ML-StoredValueCards.114000125.xml` |
| 111 | `ML-StoredValueCards.114000126.xml` |
| 112 | `ML-StructuringAvoidReportThreshold.116000058.xml` |
| 113 | `ML-StructuringAvoidReportThreshold.116000062.xml` |
| 114 | `ML-StructuringAvoidReportThreshold.116000063.xml` |
| 115 | `ML-StructuringDepWDMixedMIs.116000043.xml` |
| 116 | `ML-StructuringDepWDMixedMIs.116000066.xml` |
| 117 | `ML-StructuringDepWDMixedMIs.118860016.xml` |
| 118 | `ML-TerroristFinancing.114000122.xml` |
| 119 | `ML-TerroristFinancing.114000123.xml` |
| 120 | `ML-TerroristFinancing.114000124.xml` |
| 121 | `ML-TerroristFinancing.118860004.xml` |

---

## Category 4: Files with No `@MINER@` Data Objects in SQL (39 files)

These scenarios use ONLY `@BUSINESS@` and `@MANTAS@` tables (and/or CTEs) in their SQL queries -- no `@MINER@` schema objects.

| # | File |
|---|------|
| 1 | `FR-AcctChngFBDisburse.115400004.xml` |
| 2 | `FR-AcctChngFBDisburse.115400006.xml` |
| 3 | `FR-AcctsWithMultADChgs.118745207.xml` |
| 4 | `FR-AnomATMBCEMultiLocations.114400012.xml` |
| 5 | `FR-AnomATMBCEMultiLocations.114400013.xml` |
| 6 | `FR-AnomATMBCForeignTrans.117350028.xml` |
| 7 | `FR-AnticipateProfileIncome.117350026.xml` |
| 8 | `FR-CheckFraudInNewAcct.118745009.xml` |
| 9 | `FR-CIBPreviousAverageDebitActivity.114400014.xml` |
| 10 | `FR-DishonoredChecks.115400005.xml` |
| 11 | `FR-ElectTransInvEmpl.115400008.xml` |
| 12 | `FR-EmployeeJournals.115400009.xml` |
| 13 | `FR-EmpTransBelowLimit.118745004.xml` |
| 14 | `FR-EmpTransBelowLimit.118745007.xml` |
| 15 | `FR-EscalationDisb.115600003.xml` |
| 16 | `FR-EsclInATMActWDToDailyLimit.118745010.xml` |
| 17 | `FR-EsclInATMActWDToDailyLimit.118745012.xml` |
| 18 | `FR-ExcessDebitCardPurchase.115500002.xml` |
| 19 | `FR-ExtMatchedNames.117350018.xml` |
| 20 | `FR-ExtMatchedNames.117350020.xml` |
| 21 | `FR-FTNCuEnExternal.117350041.xml` |
| 22 | `FR-HRTransFocalHRE.117350030.xml` |
| 23 | `FR-HRTransHRCounterParty.117350035.xml` |
| 24 | `FR-HRTransHRCounterParty.117350038.xml` |
| 25 | `FR-JournalBetUnrelatedAC.117350014.xml` |
| 26 | `FR-MultACChgtoSameAD.118745209.xml` |
| 27 | `FR-RapidMvmtFundsAllActivity.117350013.xml` |
| 28 | `FR-RapidMvmtFundsAllActivity.118745398.xml` |
| 29 | `FR-RepeatedInquiry.118745013.xml` |
| 30 | `IML-HighRiskEFT-dINST.114000026.xml` |
| 31 | `IML-HighRiskInstructions-dINST.114000021.xml` |
| 32 | `IML-OffsettingTrade-dINST.114000052.xml` |
| 33 | `ML-BeneOwnerChngSurrender-dNSR.115600005.xml` |
| 34 | `ML-CashTransSignificantCash.116000048.xml` |
| 35 | `ML-CustBorrowNewPolicy-dNSR.115600004.xml` |
| 36 | `ML-HRTransFocalHRE.114000064.xml` |
| 37 | `ML-JournalBetUnrelatedAC.114000051.xml` |
| 38 | `ML-RapidMvmtFundsFTN.116000042.xml` |
| 39 | `ML-RapidMvmtFundsFTN.116000052.xml` |

---

## Summary

| Category | Count | Description |
|----------|-------|-------------|
| **Functions** | 12 | SQL calls `@MINER@.F_*` functions |
| **Pipeline** | 1 | SQL uses `@MINER@.ANOMATMEXCESS_PIPELINE` |
| **@MINER@ Tables** | 121 | SQL references `@MINER@.DSVIEW*`, `@MINER@.External_Entity`, `@MINER@.cash_trxn`, etc. |
| **No @MINER@ objects** | 39 | SQL uses only `@BUSINESS@`/`@MANTAS@` tables and/or CTEs |
| **Total** | **173** | |
