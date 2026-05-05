"""Multibagger Radar (v0.3) — 米株中小型の週次スクリーニング系統.

A 系統（既存の Daily Watch）とは独立して動作する。
週次（月曜起動）で動作することを前提とする。

主要モジュール:
- fmp: FMP API クライアント（キャッシュ付き）
- universe: ユニバース生成（時価総額バンド + exclude/include）
- fundamentals: 四半期ファンダ取得
- momentum: 株価モメンタム算出
- score: スコアリング・状態分類
- email: 週次サマリメール生成
- universe_state: ユニバースの古さ判定 / 週次状態遷移
- runner: 週次ワンショット実行
"""
