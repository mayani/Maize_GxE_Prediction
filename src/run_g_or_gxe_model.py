import gc
import argparse
from pathlib import Path

import pandas as pd
import lightgbm as lgbm
from sklearn.decomposition import TruncatedSVD

from preprocessing import create_field_location
from evaluate import create_df_eval, avg_rmse, feat_imp


parser = argparse.ArgumentParser()
parser.add_argument('--cv', type=int, choices={0, 1, 2}, required=True)
parser.add_argument('--fold', type=int, choices={0, 1, 2, 3, 4}, required=True)
parser.add_argument('--seed', type=int, required=True)
parser.add_argument('--model', choices={'G', 'GxE'}, required=True)
parser.add_argument('--A', action='store_true', default=False)
parser.add_argument('--D', action='store_true', default=False)
parser.add_argument('--E', action='store_true', default=False)
parser.add_argument('--svd', action='store_true', default=False)
parser.add_argument('--n_components', type=int, default=100)
parser.add_argument('--lag_features', action='store_true', default=False)
args = parser.parse_args()

OUTPUT_PATH = Path(f'output/cv{args.cv}')

if args.model == 'G':
    outfile = OUTPUT_PATH / f'oof_g_model_fold{args.fold}_seed{args.seed}'
    print('Using G model.')
else:
    print('Using GxE model.')
    outfile = OUTPUT_PATH / f'oof_gxe_model_fold{args.fold}_seed{args.seed}'


def preprocess_g(df, kinship, individuals: list):
    df.columns = [x[:len(x) // 2] for x in df.columns]  # fix duplicated column names
    df.index = df.columns
    df = df[df.index.isin(individuals)]  # filter rows
    df = df[[col for col in df.columns if col in individuals]]  # filter columns
    df.index.name = 'Hybrid'
    df.columns = [f'{x}_{kinship}' for x in df.columns]
    return df


def preprocess_kron(df, kinship):
    df[['Env', 'Hybrid']] = df['id'].str.split(':', expand=True)
    df = df.drop('id', axis=1).set_index(['Env', 'Hybrid'])
    df.columns = [f'{x}_{kinship}' for x in df.columns]
    # print(df.info(), '\n')
    # df[df.columns] = np.array(df.values, dtype=np.float32)  # downcast is too slow
    # print(df.info(), '\n')
    return df


def prepare_gxe(kinship):
    kron = pd.read_feather(OUTPUT_PATH / f'kronecker_{kinship}.arrow')
    kron = preprocess_kron(kron, kinship=kinship)
    return kron


if __name__ == '__main__':
    
    # load targets
    ytrain = pd.read_csv(OUTPUT_PATH / f'ytrain_fold{args.fold}_seed{args.seed}.csv')
    yval = pd.read_csv(OUTPUT_PATH / f'yval_fold{args.fold}_seed{args.seed}.csv')
    individuals = ytrain['Hybrid'].unique().tolist() + yval['Hybrid'].unique().tolist()
    individuals = list(dict.fromkeys(individuals))  # take unique but preserves order (python 3.7+)
    print('# unique individuals:', len(individuals))

    # load kinships or kroneckers
    kinships = []
    kroneckers = []
    if args.A:
        print('Using A matrix.')
        outfile = f'{outfile}_A'
        if args.model == 'G':
            A = pd.read_csv('output/kinship_additive.txt', sep='\t')
            A = preprocess_g(A, 'A', individuals)
            kinships.append(A)
        else:
            kroneckers.append(prepare_gxe('additive'))

    if args.D:
        print('Using D matrix.')
        outfile = f'{outfile}_D'
        if args.model == 'G':
            D = pd.read_csv('output/kinship_dominant.txt', sep='\t')
            D = preprocess_g(D, 'D', individuals)
            kinships.append(D)
        else:
            kroneckers.append(prepare_gxe('dominant'))

    if args.E:
        if args.model == 'G':
            print('Using E matrix.')
            outfile = f'{outfile}_E'
            Etrain = pd.read_csv(OUTPUT_PATH / f'xtrain_fold{args.fold}_seed{args.seed}.csv')
            Eval = pd.read_csv(OUTPUT_PATH / f'xval_fold{args.fold}_seed{args.seed}.csv')
        else:
            raise Exception('G+E+GxE is not implemented.')
        
    print('Using fold', args.fold)

    if (args.model == 'G' and len(kinships) == 0) or (args.model == 'GxE' and len(kroneckers) == 0):
        raise Exception('Choose at least one matrix.')
    
    # concat dataframes and bind target
    if args.model == 'G':
        K = pd.concat(kinships, axis=1)
        xtrain_temp = pd.merge(ytrain, K, on='Hybrid', how='left')
        print("After merge, before dropna:", xtrain_temp.shape)
        print("NaNs per column:")
        print(xtrain_temp.isnull().sum())
        xtrain = pd.merge(ytrain, K, on='Hybrid', how='left').dropna().set_index(['Env', 'Hybrid'])
        xval = pd.merge(yval, K, on='Hybrid', how='left').dropna().set_index(['Env', 'Hybrid'])
        del kinships
        gc.collect()
    else:
        kron = pd.concat(kroneckers, axis=1)
        del kroneckers
        xtrain = pd.merge(ytrain, kron, on=['Env', 'Hybrid'], how='inner')
        xval = pd.merge(yval, kron, on=['Env', 'Hybrid'], how='inner')
        del kron
        gc.collect()

    # split x, y
    ytrain = xtrain['Yield_Mg_ha']
    del xtrain['Yield_Mg_ha']
    yval = xval['Yield_Mg_ha']
    del xval['Yield_Mg_ha']
    gc.collect()

    # include E matrix if requested
    if args.E:
        xtrain = xtrain.merge(Etrain, on=['Env', 'Hybrid'], how='left').copy().set_index(['Env', 'Hybrid'])
        xval = xval.merge(Eval, on=['Env', 'Hybrid'], how='left').copy().set_index(['Env', 'Hybrid'])
        lag_cols = xtrain.filter(regex='_lag', axis=1).columns
        if len(lag_cols) > 0:
            xtrain = xtrain.drop(lag_cols, axis=1)
            xval = xval.drop(lag_cols, axis=1)

    print("Xtrain Head: \n")
    print(xtrain.head)
    # bind lagged yield features
    no_lags_cols = [x for x in xtrain.columns if x[1] not in ['(Intercept)', 'Env', 'Hybrid']]
    print(f"Selected {len(no_lags_cols)} columns (excluding 'Env' and 'Hybrid')")
    print(no_lags_cols[:5])  # peek at first few

    
    
    if args.lag_features:
        outfile = f'{outfile}_lag_features'
        xtrain_lag = pd.read_csv(OUTPUT_PATH / f'xtrain_fold{args.fold}_seed{args.seed}.csv', usecols=lambda x: 'yield_lag' in x or x in ['Env', 'Hybrid']).set_index(['Env', 'Hybrid'])
        xval_lag = pd.read_csv(OUTPUT_PATH / f'xval_fold{args.fold}_seed{args.seed}.csv', usecols=lambda x: 'yield_lag' in x or x in ['Env', 'Hybrid']).set_index(['Env', 'Hybrid'])
        xtrain = xtrain.merge(xtrain_lag, on=['Env', 'Hybrid'], how='inner').copy()
        xval = xval.merge(xval_lag, on=['Env', 'Hybrid'], how='inner').copy()
    
    if args.model == 'GxE':
        if 'Env' in xtrain.columns and 'Hybrid' in xtrain.columns:
            xtrain = xtrain.set_index(['Env', 'Hybrid'])
            xval = xval.set_index(['Env', 'Hybrid'])

    # run model
    if not args.svd:

        # add factor
        xtrain = xtrain.reset_index()
        xtrain = create_field_location(xtrain)
        xtrain['Field_Location'] = xtrain['Field_Location'].astype('category')
        xtrain = xtrain.set_index(['Env', 'Hybrid'])
        xval = xval.reset_index()
        xval = create_field_location(xval)
        xval['Field_Location'] = xval['Field_Location'].astype('category')
        xval = xval.set_index(['Env', 'Hybrid'])

        # include E matrix if requested
        if args.E:
            lag_cols = xtrain.filter(regex='_lag', axis=1).columns
            if len(lag_cols) > 0:
                xtrain = xtrain.drop(lag_cols, axis=1)
                xval = xval.drop(lag_cols, axis=1)
            xtrain = xtrain.merge(Etrain, on=['Env', 'Hybrid'], how='left').set_index(['Env', 'Hybrid'])
            xval = xval.merge(Eval, on=['Env', 'Hybrid'], how='left').set_index(['Env', 'Hybrid'])
            del Etrain, Eval
            gc.collect()

        print('Using full set of features.')
        print('# Features:', xtrain.shape[1])

        # fit
        model = lgbm.LGBMRegressor(random_state=args.seed, max_depth=3)
        model.fit(xtrain, ytrain)

        # predict
        ypred_train = model.predict(xtrain)
        ypred = model.predict(xval)

        # validate
        df_eval_train = create_df_eval(xtrain, ytrain, ypred_train)
        df_eval = create_df_eval(xval, yval, ypred)
        _ = avg_rmse(df_eval)
        
    else:
        outfile = f'{outfile}_svd{args.n_components}comps'
        print('Using svd.')
        print('# Components:', args.n_components)
        svd = TruncatedSVD(n_components=args.n_components, random_state=args.seed)
        print(xtrain[no_lags_cols].shape)
        print(xtrain[no_lags_cols].head())
        svd.fit(xtrain[no_lags_cols])  # fit but without lagged yield features
        print('Explained variance:', svd.explained_variance_ratio_.sum())

        # transform from the fitted svd
        svd_cols = [f'svd{i}' for i in range(args.n_components)]
        xtrain_svd = pd.DataFrame(svd.transform(xtrain[no_lags_cols]), columns=svd_cols, index=xtrain[no_lags_cols].index)
        xval_svd = pd.DataFrame(svd.transform(xval[no_lags_cols]), columns=svd_cols, index=xval[no_lags_cols].index)
        del svd
        gc.collect()

        # bind lagged yield features if needed
        if args.lag_features:
            xtrain = xtrain_svd.merge(xtrain_lag, on=['Env', 'Hybrid'], how='inner').copy()
            del xtrain_svd, xtrain_lag
            xval = xval_svd.merge(xval_lag, on=['Env', 'Hybrid'], how='inner').copy()
            del xval_svd, xval_lag
            gc.collect()
        else:
            xtrain = xtrain_svd.copy()
            del xtrain_svd
            xval = xval_svd.copy()
            del xval_svd
            gc.collect()

    if args.svd:

        # add factor
        xtrain = xtrain.reset_index()
        xtrain = create_field_location(xtrain)
        xtrain['Field_Location'] = xtrain['Field_Location'].astype('category')
        xtrain = xtrain.set_index(['Env', 'Hybrid'])
        xval = xval.reset_index()
        xval = create_field_location(xval)
        xval['Field_Location'] = xval['Field_Location'].astype('category')
        xval = xval.set_index(['Env', 'Hybrid'])

        model = lgbm.LGBMRegressor(random_state=args.seed, max_depth=3)
        model.fit(xtrain, ytrain)

        # predict
        ypred_train = model.predict(xtrain)
        ypred = model.predict(xval)

        # validate
        df_eval_train = create_df_eval(xtrain, ytrain, ypred_train)
        df_eval = create_df_eval(xval, yval, ypred)
        _ = avg_rmse(df_eval)

        # feature importance
        df_feat_imp = feat_imp(model)
        feat_imp_outfile = f'{outfile.replace("oof", "feat_imp")}.csv'
        df_feat_imp.to_csv(feat_imp_outfile, index=False)

    # write OOF results
    outfile = f'{outfile}.csv'
    print('Writing file:', outfile, '\n')
    df_eval.to_csv(outfile, index=False)
    df_eval_train.to_csv(outfile.replace('oof_', 'pred_train_'), index=False)
