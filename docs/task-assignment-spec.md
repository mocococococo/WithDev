# Slackでのタスク担当調整

## 動作

- WithDevで会議の議事録から新しく生成された、未完了かつ担当者未設定のタスクは、自動で担当調整を開始する。タスク生成と同じトランザクションでキューに登録する。
- 全体タスク画面の「未担当を一括打診」で、表示中の絞り込みに関係なく、チーム内の未担当・未完了タスクをまとめて打診できる。手動作成や既存のタスクも対象とする。
- タスク詳細画面の「割り当て打診」で、そのタスクだけ打診できる。タスクカードには打診ボタンを配置しない。
- 打診中のタスクはスキップし、重複したDMを送らない。ボタンの受付中は連続操作を防ぎ、受付件数とスキップ件数を表示する。Slack未接続の場合は設定を促す。
- 終了済みの未担当タスクは、ボタン操作によって新しい打診を開始できる。前回の履歴は保存し、古いSlackボタンの操作は新しい打診に反映しない。自動再通知・自動再開は行わない。
- エージェントはチームの有効なメンバー全員を候補として順位付けする。未完了・進行中・打診中のタスク数、期限、過去の担当タスクのタイトルと本文を判断材料とする。領域の情報がない場合、経験を捏造しない。
- 候補者へ順位順に1人ずつ、Slackの個別DMで打診する。WithDevに登録されたメールアドレスで同じSlackワークスペースのユーザーを照合する。
- DMにはタスクの内容、希望期限、日付選択と「担当する」「期限を相談する」「今回は難しい」のボタンを表示する。
- 「担当する」で本人を担当者として登録し、選択された期限をWithDevに反映する。タスクの進捗は変更しない。
- 「期限を相談する」で相談に進む。返信は同じスレッドで受け取り、エージェントが質問に答えたり希望期限を確認したりする。進行中の打診が1件だけなら、スレッド外のDM返信も受け取る。
- 会話から抽出した期限は提案として保持し、本人が「担当する」を押すまでタスクを変更しない。日付は日本時間のその日の終わりとして保存する。
- 辞退された場合は次の候補に打診する。全候補が辞退したら、タスクを未割り当てのままにする。
- 無応答は72時間待つ。その後は打診を終了し、再通知も次候補への打診も行わず、タスクを未割り当てのままにする。相談が続く場合はエージェントの直近の返信から72時間待つ。
- タスク詳細画面に担当調整の状態、打診先、返答期限を表示する。担当が確定したら画面のタスク情報も再取得する。

## 整合性と障害時の動作

- Slackの署名とリクエスト時刻を検証する。操作したワークスペース、本人、DM、スレッドを打診内容と照合する。
- チームを退会したメンバーや、無効化されたSlack接続からの受諾は反映しない。
- 打診後に担当者・タスク内容・期限が手動変更された場合、古い打診は取り消す。
- Slackのコールバックは重複排除してDBに保存後、応答する。AIやSlackへの送信はワーカーで実行する。
- 期限内にDBへ保存された返答は、ワーカーの停止により処理が遅れても有効として扱う。
- AIの候補一覧にチーム外のID、重複、欠落があればエラーとし、送信しない。
- メール照合・DM送信・AI選定のエラーは担当調整の失敗として記録する。
- 送信途中の停止など、Slackに届いたか判断できない場合は失敗とし、自動再送しない。タスクの未割り当て状態を維持する。
- ワーカーはDBのアドバイザリロックで1プロセスだけ実行し、会話の処理順序を維持する。

## 導入

1. バックエンドの環境変数に `SLACK_SIGNING_SECRET` を設定する。既存のGemini・Slack OAuth設定も必要。
2. `cd backend` で `alembic upgrade head` を実行する。
3. SlackアプリのBot Token Scopesに既存の権限に加えて `im:write`、`im:history`、`users:read.email` を追加し、WithDevからSlackを再接続する。
4. SlackアプリのInteractivityを有効にし、Request URLを `https://<backend>/api/slack/interactions` に設定する。
5. Event Subscriptionsを有効にし、Request URLを `https://<backend>/api/slack/events` に設定する。Bot Eventに `message.im` を追加する。
6. APIとは別の常駐プロセスとして `python -m app.workers.task_assignment` を起動する。

ワーカーを起動すると保存済みの未処理キューから打診を開始する。ワーカーの起動前に、Slackアプリの設定と再接続を完了させる。

## 開発環境のCloud Runへの導入

`.github/workflows/deploy-backend-dev.yml` がAPIと同じイメージを使って、Cloud Run Worker Poolに常駐ワーカーをデプロイする。
初回は以下の順に設定する。Secretの値はチャット・ソースコード・GitHub Variablesに貼り付けない。

1. Slackアプリの **Basic Information → App Credentials → Signing Secret** を、開発プロジェクトのGoogle Cloud Secret Managerに保存する。例のSecret IDは `withdev-slack-signing-secret-dev`。SlackのClient Secretとは別の値。
2. そのSecretの権限で、既存の実行サービスアカウント `withdev-backend-runtime@<GCP_PROJECT_ID_DEV>.iam.gserviceaccount.com` に `Secret Manager Secret Accessor` を付与する。
3. GitHubリポジトリの **Settings → Secrets and variables → Actions → Variables** に以下を設定する。

   | Variable | 値 |
   |---|---|
   | `SLACK_SIGNING_SECRET_SECRET_DEV` | Secret ID（例：`withdev-slack-signing-secret-dev`）。Signing Secretの値ではない |
   | `DEPLOY_ASSIGNMENT_WORKER_DEV` | 初回は `false` |
   | `CLOUD_RUN_ASSIGNMENT_WORKER_POOL_DEV` | 任意。省略時は `withdev-assignment-dev` |

4. このコード変更を反映して、GitHub Actionsの **Deploy Backend Development** を実行する。マイグレーションとAPIを更新し、APIに `SLACK_SIGNING_SECRET` が渡る。この段階ではワーカーを起動しない。
5. 上の導入手順に従い、Slackの権限追加、Interactivity・Event SubscriptionsのURL設定、WithDevからのSlack再接続を完了させる。URLにはフロントエンドではなくバックエンドのURLを使う。
6. `DEPLOY_ASSIGNMENT_WORKER_DEV` を `true` に変更し、**Deploy Backend Development** をもう一度実行する。API更新後にWorker Poolを作成・更新し、ワーカーを1台起動する。以降、`true` の間はバックエンドのデプロイ時にワーカーも更新する。
7. Cloud Runの **Worker pools → withdev-assignment-dev → Logs** で `Assignment worker acquired leadership` を確認する。打診ボタンを押したタスクの状態が「担当候補を選定中」「返答待ち」に進むことを確認する。

デプロイ用サービスアカウントにはWorker Poolの作成・更新権限（例：Cloud Run Developer）と、実行サービスアカウントのService Account User権限が必要。既存のカスタムロールを使っている場合はWorker Pool操作の権限も追加する。

Worker Poolは継続的なバックグラウンド処理向けで、HTTP待受は不要。APIのリクエストがなくてもキューを処理する。設定は1 vCPU・512MiB・1台で、待機中も稼働時間に応じて課金される。[Worker Poolの公式説明](https://docs.cloud.google.com/run/docs/deploy-worker-pools)、[料金](https://cloud.google.com/run/pricing)。

`DEPLOY_ASSIGNMENT_WORKER_DEV=false` はデプロイ工程をスキップする設定であり、起動済みのWorker Poolは停止しない。停止する場合はCloud RunのWorker Poolのインスタンス数を0に変更する。

デプロイの切り替え中に新旧ワーカーが重なっても、新しいワーカーはDBロックの取得を待ち、同時にキューを処理しない。終了シグナルを受け取ったワーカーは新しい処理を開始せず、DB接続を解放する。

## テスト

```bash
cd backend
python -m unittest discover -s tests
```

PostgreSQLでの統合テストには、使い捨てのテスト専用DBを指定する。DB名に `test` が必要で、テーブルを作成・削除する。

```bash
ASSIGNMENT_TEST_DATABASE_URL=postgresql+asyncpg://user:password@127.0.0.1:5432/withdev_test \
  python -m unittest discover -s tests -p test_task_assignment.py
```

SlackとGeminiはモック化し、実際のDM送信やAIへの送信は行わない。
