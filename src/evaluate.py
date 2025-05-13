import pandas as pd
from sklearn.metrics import root_mean_squared_error


def create_df_eval(xval, yval, ypred):
    df_eval = pd.DataFrame()
    df_eval['Env'] = xval.index.get_level_values(0)
    df_eval['Hybrid'] = xval.index.get_level_values(1)
    df_eval['ytrue'] = list(yval)
    df_eval['ypred'] = ypred
    return df_eval
    

def avg_rmse(df_eval, verbose=True):
    rmse_per_group = (
        df_eval
        .groupby('Env')
        .apply(lambda x: root_mean_squared_error(x['ytrue'], x['ypred']))
    )
    avg_rmse_ = sum(rmse_per_group) / len(rmse_per_group)

    if verbose:
        print(rmse_per_group)
        print('RMSE:', avg_rmse_)
        
    return avg_rmse_


def feat_imp(model):
    d = dict(zip(model.feature_name_, model.feature_importances_))
    d = dict(sorted(d.items(), key=lambda x: -x[1]))
    df = pd.DataFrame(d.items(), columns=['feature', 'imp'])
    return df
