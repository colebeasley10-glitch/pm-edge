--- /tmp/watcher_original.py	2026-09-15 14:44:38.362954138 +0000
+++ /home/claude/pm-edge-extract/pm-edge-main/watcher.py	2026-09-15 14:43:53.601772222 +0000
@@ -972,6 +972,7 @@
     accepted_same_sport_time_confirmed = 0
     rejected_low_score_missing_time = 0
     rejected_low_score_bad_time = 0
+    rejected_strong_bad_time = 0
     accepted_unknown_sport = 0
     top_same_sport_team_score = 0.0
     top_same_sport_confidence = 0.0
@@ -1036,10 +1037,20 @@
             if team_score < TEAM_PAIR_MIN_SAME_SPORT:
                 continue
 
+            time_ok, delta_h = low_score_time_confirmed(market, em.event)
+
             if team_score >= TEAM_PAIR_STRONG_SAME_SPORT:
+                # A strong team-name match does NOT guarantee this is the same
+                # specific contest: two teams can play more than once in a
+                # short window (doubleheaders, back-to-back series). When both
+                # timestamps are available, require them to agree even for a
+                # strong name match -- otherwise a correctly-named but wrong
+                # game can be priced off the wrong sportsbook event.
+                if delta_h is not None and not time_ok:
+                    rejected_strong_bad_time += 1
+                    continue
                 accepted_same_sport_strong += 1
             else:
-                time_ok, delta_h = low_score_time_confirmed(market, em.event)
                 if delta_h is None:
                     rejected_low_score_missing_time += 1
                     continue
@@ -1047,8 +1058,9 @@
                     rejected_low_score_bad_time += 1
                     continue
                 accepted_same_sport_time_confirmed += 1
-                if best_confirmed_time_delta_hours is None or delta_h < best_confirmed_time_delta_hours:
-                    best_confirmed_time_delta_hours = delta_h
+
+            if delta_h is not None and (best_confirmed_time_delta_hours is None or delta_h < best_confirmed_time_delta_hours):
+                best_confirmed_time_delta_hours = delta_h
 
             accepted_same_sport += 1
         else:
@@ -1262,6 +1274,7 @@
             f"Top same-sport confidence: {top_same_sport_confidence:.2f}\n"
             f"Low-score missing-time rejects: {rejected_low_score_missing_time}\n"
             f"Low-score bad-time rejects: {rejected_low_score_bad_time}\n"
+            f"Strong-name bad-time rejects: {rejected_strong_bad_time}\n"
             f"Kalshi horizon filtered: {kalshi_horizon_filtered}\n"
             f"Sportsbook horizon filtered: {sportsbook_horizon_filtered}\n"
             f"Near-term candidates: {len(candidates)}\n"
@@ -1303,6 +1316,7 @@
         "accepted_same_sport_time_confirmed": accepted_same_sport_time_confirmed,
         "rejected_low_score_missing_time": rejected_low_score_missing_time,
         "rejected_low_score_bad_time": rejected_low_score_bad_time,
+        "rejected_strong_bad_time": rejected_strong_bad_time,
         "accepted_unknown_sport": accepted_unknown_sport,
         "top_same_sport_team_score": round(top_same_sport_team_score, 4),
         "top_same_sport_confidence": round(top_same_sport_confidence, 4),
