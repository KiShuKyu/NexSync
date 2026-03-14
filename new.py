from core.config import Config
from core.auth import NexSyncAuth, AuthError
from core.watcher import FileWatcher
from core.network import NetworkManager
from core.git_engine import GitEngine
from core.sharing import ShareManager
from core.conflict import ConflictResolver
from core.pairing import PairingManager



python -c "
   from core.database import NexSyncDB
   NexSyncDB.save_credentials(
       url='https://vbyrqfyzdgtmovhrdnog.supabase.co',
       key='your-anon-key'
   )
   "

python -c "
   from core.database import NexSyncDB
   from core.auth import NexSyncAuth
   db = NexSyncDB()
   auth = NexSyncAuth(db)
   auth.sign_in('zoloronoloa@gmail.com', 'yourpassword')
   db.register_device(sync_folder='/Users/you/NexSync')
   print('done')
   "